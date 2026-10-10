import os
import sys
import math
from collections import OrderedDict
from pathlib import Path

from PyQt6.QtCore import (Qt, QSize, QTimer, QThread, pyqtSignal, QObject,
                          QRunnable, QThreadPool, QAbstractListModel, QModelIndex, QEvent,
                          QPointF, QRectF, QSettings, QItemSelectionModel)
from PyQt6.QtGui import (QAction, QImageReader, QPixmap, QIcon, QMovie,
                         QColor, QPainter, QFont)
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QLabel,
    QPushButton, QVBoxLayout, QHBoxLayout, QSplitter, QLineEdit, QComboBox,
    QListWidget, QListWidgetItem, QListView, QFileDialog, QMessageBox,
    QAbstractItemView, QCheckBox, QTabBar, QDialog, QInputDialog)
from library import Library, scan_folder

APP_VERSION = '1.3.8'


class Scan(QThread):
    result = pyqtSignal(str, object, str)
    def __init__(self, root, parent):
        super().__init__(parent)
        self.root = root
    def run(self):
        try:
            self.result.emit(self.root, scan_folder(Path(self.root)), '')
        except Exception as error:
            self.result.emit(self.root, [], str(error))


class ThumbSignal(QObject):
    ready = pyqtSignal(int, int, object)


class ThumbTask(QRunnable):
    def __init__(self, generation, row, path, signal):
        super().__init__()
        self.generation, self.row, self.path, self.signal = generation, row, path, signal
    def run(self):
        reader = QImageReader(self.path)
        reader.setAutoTransform(True)
        size = reader.size()
        if size.isValid():
            reader.setScaledSize(size.scaled(160, 112, Qt.AspectRatioMode.KeepAspectRatio))
        image = reader.read()
        if not image.isNull():
            image = image.scaled(160,112,Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.SmoothTransformation)
        self.signal.ready.emit(self.generation, self.row, image)


class Thumbnails(QAbstractListModel):
    def __init__(self, parent):
        super().__init__(parent)
        self.rows, self.root, self.generation = [], Path('.'), 0
        self.cache, self.pending = OrderedDict(), set()
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(2)
        self.signals = ThumbSignal()
        self.signals.ready.connect(self.ready)
        pix = QPixmap(160,112)
        pix.fill(QColor('#252a32'))
        self.placeholder = QIcon(pix)
    def reset(self, root, rows):
        self.beginResetModel()
        self.generation += 1
        self.pool.clear()
        self.rows, self.root = rows, root
        self.cache.clear()
        self.pending.clear()
        self.endResetModel()
    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)
    def data(self, index, role):
        if not index.isValid() or index.row() >= len(self.rows):
            return None
        row = index.row()
        if role == Qt.ItemDataRole.DisplayRole:
            return Path(self.rows[row][1]).name
        if role == Qt.ItemDataRole.ToolTipRole:
            return self.rows[row][1]
        if role == Qt.ItemDataRole.DecorationRole:
            if row in self.cache:
                self.cache.move_to_end(row)
                return self.cache[row]
            if row not in self.pending:
                self.pending.add(row)
                self.pool.start(ThumbTask(self.generation,row,str(self.root/self.rows[row][1]),self.signals))
            return self.placeholder
    def ready(self, generation, row, image):
        if generation != self.generation:
            return
        self.pending.discard(row)
        self.cache[row] = QIcon(QPixmap.fromImage(image)) if not image.isNull() else self.placeholder
        while len(self.cache)>384:
            self.cache.popitem(last=False)
        idx = self.index(row)
        self.dataChanged.emit(idx,idx,[Qt.ItemDataRole.DecorationRole])


class Viewer(QWidget):
    step = pyqtSignal(int)
    def __init__(self):
        super().__init__()
        self.setMinimumSize(280,280)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.pixmap = QPixmap()
        self.movie = None
        self.source_size=QSize()
        self.source_transform=0
        self.tile_source=None
        self.detail_timer=QTimer(self)
        self.detail_timer.setSingleShot(True)
        self.detail_timer.timeout.connect(self.render_detail)
        self.message = '이미지를 위한 조용한 공간\n\n오른쪽에서 이미지 폴더를 열어 시작하세요.'
        self.wheel_amount = 0
        self.path=None
        self.zoom=1.0
        self.pan=QPointF()
        self.drag_position=None
        self.setToolTip('Ctrl+휠: 확대/축소 · 확대 후 드래그: 이동 · 0 또는 더블클릭: 화면 맞춤')
    def show_path(self, path, force=False):
        if path is not None and self.path==path and not force:
            return
        self.detail_timer.stop()
        self.source_size=QSize()
        self.tile_source=None
        self.path=path
        self.reset_zoom()
        if self.movie:
            self.movie.stop()
            self.movie.setFileName('')
            self.movie.deleteLater()
            self.movie = None
        self.pixmap = QPixmap()
        self.message = '조건에 맞는 이미지가 없습니다.\n필터를 바꾸거나 해제해 보세요.' if path is None else ''
        if path:
            reader = QImageReader(str(path))
            reader.setAutoTransform(True)
            if reader.supportsAnimation() and reader.imageCount()!=1:
                self.movie = QMovie(str(path))
                self.movie.frameChanged.connect(self.frame)
                self.movie.start()
                if not self.movie.isValid():
                    self.message = '이미지를 열 수 없습니다.\n파일이 손상되었거나 이동되었을 수 있습니다.'
            elif reader.size().isValid() and reader.size().width()*reader.size().height()>16_000_000:
                self.source_transform=reader.transformation().value
                size=reader.size()
                self.source_size=QSize(size.height(),size.width()) if self.source_transform & 4 else size
                self.render_detail()
            else:
                self.pixmap = QPixmap.fromImage(reader.read())
                if self.pixmap.isNull():
                    self.message = '이미지를 열 수 없습니다.\n파일이 손상되었거나 이동되었을 수 있습니다.'
        self.update()
    def render_detail(self):
        if not self.path or not self.source_size.isValid():
            return
        fit=self.dimensions()
        width,height=fit.width()*self.zoom,fit.height()*self.zoom
        left,top=(self.width()-width)/2+self.pan.x(),(self.height()-height)/2+self.pan.y()
        sx,sy=width/self.source_size.width(),height/self.source_size.height()
        x=max(0,math.floor((-left-32)/sx));y=max(0,math.floor((-top-32)/sy))
        x-=x%2;y-=y%2 # WebP crop origins use even coordinates.
        right=min(self.source_size.width(),math.ceil((self.width()-left+32)/sx))
        bottom=min(self.source_size.height(),math.ceil((self.height()-top+32)/sy))
        if right<=x or bottom<=y:
            return
        dpr=self.devicePixelRatioF()
        try:
            # Small scaled decodes retain Qt's format/color/orientation handling.
            # At high zoom request only the visible source rectangle via WIC.
            if width*height*dpr*dpr<=8_000_000:
                reader=QImageReader(str(self.path));reader.setAutoTransform(True)
                target=QSize(max(1,math.ceil(width*dpr)),max(1,math.ceil(height*dpr)))
                if self.source_transform & 4:
                    target.transpose()
                reader.setScaledSize(target)
                image=reader.read()
                if image.isNull():
                    from viewport_image import viewport
                    image=viewport(self.path,(0,0,self.source_size.width(),self.source_size.height()),
                        (max(1,math.ceil(width*dpr)),max(1,math.ceil(height*dpr))),self.source_transform)
                tile=QRectF(0,0,self.source_size.width(),self.source_size.height())
            else:
                from viewport_image import viewport
                image=viewport(self.path,(x,y,right-x,bottom-y),
                    (max(1,math.ceil((right-x)*sx*dpr)),max(1,math.ceil((bottom-y)*sy*dpr))),self.source_transform)
                tile=QRectF(x,y,right-x,bottom-y)
            self.pixmap=QPixmap.fromImage(image)
            self.tile_source=tile
            self.message=''
        except Exception as error:
            # Keep the last valid display while reporting a detail-read failure.
            self.message='이미지 표시 실패: '+str(error)
        self.update()
    def request_detail(self):
        if self.source_size.isValid():
            self.detail_timer.start(60)
    def frame(self):
        self.pixmap = self.movie.currentPixmap()
        self.update()
    def dimensions(self):
        if self.source_size.isValid():
            return self.source_size.scaled(self.size()-QSize(32,32),Qt.AspectRatioMode.KeepAspectRatio)
        if self.pixmap.isNull():
            return QSize(1,1)
        return self.pixmap.size().scaled(self.size()-QSize(32,32),Qt.AspectRatioMode.KeepAspectRatio)
    def clamp_pan(self):
        size=self.dimensions()
        limit_x=max(0,(size.width()*self.zoom-self.width())/2+16)
        limit_y=max(0,(size.height()*self.zoom-self.height())/2+16)
        self.pan=QPointF(max(-limit_x,min(limit_x,self.pan.x())),max(-limit_y,min(limit_y,self.pan.y())))
    def reset_zoom(self):
        self.zoom=1.0
        self.pan=QPointF()
        self.drag_position=None
        self.unsetCursor()
        self.request_detail()
        self.update()
    def actual_size(self):
        if self.pixmap.isNull():
            return
        self.zoom=(self.source_size.width() if self.source_size.isValid() else self.pixmap.width())/max(1,self.dimensions().width())
        self.pan=QPointF()
        self.clamp_pan()
        self.request_detail()
        self.update()
    def change_zoom(self,factor,anchor=None):
        if self.pixmap.isNull():
            return
        old=self.zoom
        self.zoom=max(0.1,min(max(32.0,self.source_size.width()/max(1,self.dimensions().width())*4),old*factor))
        anchor=anchor or QPointF(self.width()/2,self.height()/2)
        offset=anchor-QPointF(self.width()/2,self.height()/2)
        self.pan=offset-(offset-self.pan)*(self.zoom/old)
        self.clamp_pan()
        self.setCursor(Qt.CursorShape.OpenHandCursor if self.zoom>1 else Qt.CursorShape.ArrowCursor)
        self.request_detail()
        self.update()
    def resizeEvent(self,event):
        self.clamp_pan()
        self.request_detail()
        super().resizeEvent(event)
    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor('#101216'))
        if not self.pixmap.isNull():
            size = self.dimensions()
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            width,height=size.width()*self.zoom,size.height()*self.zoom
            x,y=(self.width()-width)/2+self.pan.x(),(self.height()-height)/2+self.pan.y()
            if self.source_size.isValid() and self.tile_source is not None:
                tile=self.tile_source
                sx,sy=width/self.source_size.width(),height/self.source_size.height()
                target=QRectF(x+tile.x()*sx,y+tile.y()*sy,tile.width()*sx,tile.height()*sy)
            else:
                target=QRectF(x,y,width,height)
            p.drawPixmap(target,self.pixmap,QRectF(self.pixmap.rect()))
            if self.message:
                p.setPen(QColor('#ffbcad'))
                p.drawText(self.rect().adjusted(20,20,-20,-20),Qt.AlignmentFlag.AlignTop|Qt.TextFlag.TextWordWrap,self.message)
            if abs(self.zoom-1)>0.001:
                p.fillRect(12,self.height()-40,290,28,QColor(16,18,22,220))
                p.setPen(QColor('#d3ddeb'))
                p.drawText(22,self.height()-21,f'맞춤 대비 {self.zoom:.1f}×  ·  드래그 이동  ·  0 맞춤')
        else:
            p.setPen(QColor('#959ba8'))
            p.drawText(self.rect().adjusted(25,25,-25,-25),Qt.AlignmentFlag.AlignCenter,self.message)
    def wheelEvent(self, event):
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            delta=event.angleDelta().y() or event.pixelDelta().y()
            self.change_zoom(1.2**(delta/120),event.position())
            self.wheel_amount=0
            event.accept()
            return
        self.wheel_amount += event.angleDelta().y()
        if abs(self.wheel_amount)>=120:
            self.step.emit(-1 if self.wheel_amount>0 else 1)
            self.wheel_amount = 0
        event.accept()
    def mousePressEvent(self, event):
        self.setFocus()
        if event.button()==Qt.MouseButton.LeftButton and self.zoom>1:
            self.drag_position=event.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
    def mouseMoveEvent(self,event):
        if self.drag_position is not None:
            self.pan+=event.position()-self.drag_position
            self.drag_position=event.position()
            self.clamp_pan()
            self.request_detail()
            self.update()
    def mouseReleaseEvent(self,event):
        self.drag_position=None
        self.setCursor(Qt.CursorShape.OpenHandCursor if self.zoom>1 else Qt.CursorShape.ArrowCursor)
    def mouseDoubleClickEvent(self,event):
        self.reset_zoom()


STYLE = '''
QMainWindow,QWidget {background:#1a1e25;color:#e8eaf0;font-family:'Segoe UI';font-size:13px;}
QMenuBar,QMenu {background:#1a1e25;} QMenu::item:selected {background:#3a4556;}
QLabel#brand {font-size:24px;font-weight:700;color:#f2f4f9;}
QLabel#muted {color:#969fad;font-size:12px;}
QLabel#section {font-weight:600;color:#cdd3de;}
QLineEdit,QComboBox {background:#252b35;border:1px solid #37404e;border-radius:7px;padding:8px;}
QLineEdit:focus {border-color:#8db4ff;}
QPushButton {background:#2b3340;border:0;border-radius:7px;padding:8px 11px;}
QPushButton:hover {background:#3c485c;} QPushButton:disabled {color:#707887;}
QPushButton#primary {background:#adc9ff;color:#14223a;font-weight:600;}
QListWidget,QListView {background:#20252e;border:0;border-radius:8px;outline:0;}
QListWidget::item {padding:5px;} QListWidget::item:selected,QListView::item:selected {background:#354968;border-radius:6px;}
QListView::item {padding:5px;} QSplitter::handle {background:#323946;width:2px;}
QListView::item:selected {color:#ffffff;}
QCheckBox::indicator {width:14px;height:14px;border:1px solid #69778a;border-radius:3px;background:#222a35;}
QCheckBox::indicator:checked {background:#adc9ff;border:1px solid #adc9ff;}
QTabBar::tab {background:#252b35;color:#aab6c8;border:0;padding:6px 10px;}
QTabBar::tab:selected {background:#3b5070;color:#ffffff;}
QTabBar::tab:hover {background:#35445a;}
QScrollBar:vertical {background:#20252e;width:10px;} QScrollBar::handle:vertical {background:#485365;min-height:25px;border-radius:4px;}
QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical {height:0;}
QStatusBar {color:#969fad;background:#171a20;}
'''


class Window(QMainWindow):
    def __init__(self,remember=False,settings=None):
        super().__init__()
        self.library = None
        self.filters, self.rows, self.current_id = set(), [], None
        self.scan = None
        self.batch_ids=set()
        self.undo_edit=None
        self.active_tag=None
        self.filter_origin=None
        self.saved_position=None
        self.restoring=False
        self.remember=remember
        self.settings=settings if settings is not None else (QSettings('Luma','Luma') if remember else None)
        self.setWindowTitle(f'Luma {APP_VERSION} — 로컬 이미지 라이브러리')
        self.resize(1380,900)
        self.setMinimumSize(960,720)
        self.setStyleSheet(STYLE)
        self.build()
        self.setWindowIcon(QIcon(str(Path(__file__).resolve().parent/'icon.ico')))
        self.shell_identity=False
        if os.name=='nt':
            try:
                from windows_shell import configure_window
                configure_window(int(self.winId()),Path(__file__).resolve().parent)
                self.shell_identity=True
            except OSError as error:
                self.statusBar().showMessage('작업표시줄 아이콘 설정 실패: '+str(error))
        QApplication.instance().installEventFilter(self)
    def label(self, text, kind='section'):
        label=QLabel(text)
        label.setObjectName(kind)
        return label
    def button(self, text, action):
        button=QPushButton(text)
        button.clicked.connect(action)
        return button
    def menu_action(self,menu,text,shortcut,callback):
        action=QAction(text,self)
        action.setShortcut(shortcut)
        action.triggered.connect(callback)
        menu.addAction(action)
        return action
    def build(self):
        file = self.menuBar().addMenu('파일')
        self.open_action=QAction('이미지 폴더 열기…',self)
        self.open_action.setShortcut('Ctrl+O')
        self.open_action.triggered.connect(self.choose_folder)
        file.addAction(self.open_action)
        self.rescan_action=QAction('폴더 다시 읽기',self)
        self.rescan_action.setShortcut('F5')
        self.rescan_action.triggered.connect(lambda:self.open_folder(str(self.library.root)) if self.library else None)
        file.addAction(self.rescan_action)
        file.addSeparator()
        file.addAction('종료',self.close)
        edit=self.menuBar().addMenu('편집')
        self.rename_action=self.menu_action(edit,'현재 이미지 이름 변경…','F2',self.rename_current)
        self.delete_action=self.menu_action(edit,'현재 이미지 파일 삭제…','Delete',self.delete_current)
        edit.addSeparator()
        edit.addAction('모든 필터 해제',self.clear_filters)
        self.menu_action(edit,'태그 찾기','Ctrl+T',self.focus_search)
        self.menu_action(edit,'선택 태그 등록 / 해제','Ctrl+Return',self.toggle_selected)
        edit.addAction('선택 태그 이름 변경…',self.rename_tag_dialog)
        edit.addAction('선택 태그 자체 삭제…',self.delete_tag_dialog)
        edit.addAction('직전 태그 / 즐겨찾기 편집 취소 (Ctrl+Z)',self.undo_last)
        view=self.menuBar().addMenu('보기')
        self.menu_action(view,'확대','Ctrl+=',lambda:self.viewer.change_zoom(1.25))
        self.menu_action(view,'축소','Ctrl+-',lambda:self.viewer.change_zoom(0.8))
        self.menu_action(view,'화면 맞춤','Ctrl+0',lambda:self.viewer.reset_zoom())
        bookmark_menu=self.menuBar().addMenu('북마크')
        self.bookmark_action=self.menu_action(bookmark_menu,'현재 이미지 북마크 추가 / 해제','Ctrl+B',self.toggle_bookmark)
        self.menu_action(bookmark_menu,'북마크로 이동…','Ctrl+Shift+B',self.show_bookmarks)
        help_menu=self.menuBar().addMenu('도움말')
        help_menu.addAction('사용 방법',self.help)
        split=QSplitter()
        self.main_split=split
        split.setHandleWidth(7)
        split.setChildrenCollapsible(False)
        self.setCentralWidget(split)
        self.viewer=Viewer()
        self.viewer.step.connect(self.step)
        viewing=QWidget()
        viewing_layout=QVBoxLayout(viewing)
        viewing_layout.setContentsMargins(0,0,0,0)
        viewing_layout.addWidget(self.viewer,1)
        zoom_row=QHBoxLayout()
        zoom_row.addStretch()
        self.zoom_buttons=[]
        for title,callback in [('−',lambda:self.viewer.change_zoom(0.8)),('+',lambda:self.viewer.change_zoom(1.25)),('화면 맞춤',self.viewer.reset_zoom),('100%',self.viewer.actual_size)]:
            button=self.button(title,callback)
            button.setToolTip({'−':'축소','+':'확대','100%':'100% 실제 크기'}.get(title,title))
            button.setFixedHeight(30)
            zoom_row.addWidget(button)
            self.zoom_buttons.append(button)
        zoom_row.addStretch()
        viewing_layout.addLayout(zoom_row)
        split.addWidget(viewing)
        panel=QWidget()
        panel.setMinimumWidth(350)
        panel.setMaximumWidth(16777215)
        layout=QVBoxLayout(panel)
        layout.setContentsMargins(22,18,22,14)
        layout.setSpacing(10)
        outer=layout
        self.right_split=QSplitter(Qt.Orientation.Vertical)
        self.right_split.setHandleWidth(7)
        self.right_split.setChildrenCollapsible(False)
        outer.addWidget(self.right_split)
        tags_panel=QWidget()
        layout=QVBoxLayout(tags_panel)
        layout.setContentsMargins(0,0,0,0)
        self.right_split.addWidget(tags_panel)
        header=QHBoxLayout()
        header.addWidget(self.label('Luma','brand'))
        header.addStretch()
        self.open_button=self.button('폴더 열기',self.choose_folder)
        self.open_button.setObjectName('primary')
        header.addWidget(self.open_button)
        layout.addLayout(header)
        self.folder_label=self.label('내 PC의 이미지를 그대로, 한곳에서.','muted')
        layout.addWidget(self.folder_label)
        self.filename=self.label('현재 이미지')
        filename_row=QHBoxLayout()
        filename_row.addWidget(self.filename,1)
        self.favorite_button=self.button('♡',self.toggle_favorite)
        self.favorite_button.setFixedWidth(42)
        self.favorite_button.setToolTip('즐겨찾기 등록 / 해제 · R')
        filename_row.addWidget(self.favorite_button)
        layout.addLayout(filename_row)
        self.current_tags=QListWidget()
        self.current_tags.setFlow(QListView.Flow.LeftToRight)
        self.current_tags.setWrapping(True)
        self.current_tags.setMaximumHeight(72)
        self.current_tags.setMinimumHeight(52)
        self.current_tags.itemClicked.connect(self.remove_tag)
        self.current_tags.setToolTip('태그를 클릭하면 현재 이미지에서 제거합니다.')
        layout.addWidget(self.current_tags)
        input_row=QHBoxLayout()
        self.tag_input=QLineEdit()
        self.tag_input.setPlaceholderText('새 태그 · 여러 개는 쉼표로 구분')
        self.tag_input.returnPressed.connect(self.add_input)
        input_row.addWidget(self.tag_input)
        self.add_button=self.button('추가',self.add_input)
        input_row.addWidget(self.add_button)
        layout.addLayout(input_row)
        filter_row=QHBoxLayout()
        filter_row.addWidget(self.label('감상 필터 · 선택한 태그 모두 포함'))
        filter_row.addStretch()
        filter_row.addWidget(self.button('해제',self.clear_filters))
        layout.addLayout(filter_row)
        self.filter_label=self.label('전체 이미지','muted')
        self.filter_label.setWordWrap(True)
        self.filter_label.setMaximumHeight(54)
        layout.addWidget(self.filter_label)
        search_row=QHBoxLayout()
        self.search=QLineEdit()
        self.search.setPlaceholderText('태그 찾기')
        self.search.textChanged.connect(self.render_tags)
        search_row.addWidget(self.search,1)
        self.sort=QComboBox()
        self.sort.addItems(['이름 A → Z','이름 Z → A','이미지 많은 순','이미지 적은 순'])
        self.sort.currentIndexChanged.connect(self.render_tags)
        search_row.addWidget(self.sort)
        layout.addLayout(search_row)
        self.tag_tabs=QTabBar()
        for name in ['전체 태그','최근 사용','자주 사용']:
            self.tag_tabs.addTab(name)
        self.tag_tabs.setExpanding(True)
        self.tag_tabs.currentChanged.connect(self.render_tags)
        layout.addWidget(self.tag_tabs)
        self.tag_list=QListWidget()
        self.tag_list.setViewMode(QListView.ViewMode.IconMode)
        self.tag_list.setResizeMode(QListView.ResizeMode.Adjust)
        self.tag_list.setMovement(QListView.Movement.Static)
        self.tag_list.setGridSize(QSize(168,48))
        self.tag_list.setSpacing(2)
        self.tag_list.setMinimumHeight(50)
        self.tag_list.currentItemChanged.connect(self.tag_selected)
        layout.addWidget(self.tag_list,1)
        self.active_label=self.label('WASD 선택 · E 등록/해제 · F 필터 · R 즐겨찾기','muted')
        self.active_label.setWordWrap(True)
        layout.addWidget(self.active_label)
        thumbs_panel=QWidget()
        self.right_split.addWidget(thumbs_panel)
        layout=QVBoxLayout(thumbs_panel)
        layout.setContentsMargins(0,0,0,0)
        self.batch_mode=QCheckBox('다중 선택 태깅 · OFF')
        self.batch_mode.setToolTip('클릭으로 선택 추가 · 다시 클릭하면 선택 해제 · 일부만 등록된 태그는 전체에 추가, 모두 등록되면 전체 해제')
        self.batch_mode.toggled.connect(self.batch_changed)
        layout.addWidget(self.batch_mode)
        self.batch_label=self.label('','muted')
        layout.addWidget(self.batch_label)
        self.batch_label.hide()
        result_header=QHBoxLayout()
        self.count_label=self.label('이미지 0개')
        result_header.addWidget(self.count_label)
        result_header.addStretch()
        self.image_sort=QComboBox()
        self.image_sort.addItems(['파일 이름 ↑','파일 이름 ↓','크기 작은 순','크기 큰 순','날짜 오래된 순','날짜 최신 순'])
        self.image_sort.setMaximumWidth(145)
        self.image_sort.setToolTip('이미지 파일 정렬 · 날짜는 파일 수정일 기준 · F5로 파일 정보 갱신')
        self.image_sort.currentIndexChanged.connect(self.image_sort_changed)
        result_header.addWidget(self.image_sort)
        result_header.addWidget(self.button('‹',lambda:self.step(-1)))
        result_header.addWidget(self.button('›',lambda:self.step(1)))
        layout.addLayout(result_header)
        self.image_tabs=QTabBar()
        self.image_tabs.addTab('전체 사진')
        self.image_tabs.addTab('즐겨찾기')
        self.image_tabs.currentChanged.connect(self.image_sort_changed)
        layout.addWidget(self.image_tabs)
        self.thumbs=QListView()
        self.thumbs.setViewMode(QListView.ViewMode.IconMode)
        self.thumbs.setResizeMode(QListView.ResizeMode.Adjust)
        self.thumbs.setMovement(QListView.Movement.Static)
        self.thumbs.setIconSize(QSize(144,100))
        self.thumbs.setGridSize(QSize(156,132))
        self.thumbs.setSpacing(3)
        self.thumbs.setUniformItemSizes(True)
        self.thumbs.setWordWrap(False)
        self.thumbs.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.thumbs.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.model=Thumbnails(self)
        self.thumbs.setModel(self.model)
        self.thumbs.selectionModel().currentChanged.connect(self.thumbnail_selected)
        self.thumbs.selectionModel().selectionChanged.connect(self.batch_selection_changed)
        layout.addWidget(self.thumbs,3)
        layout.addWidget(self.label('← ↑ 이전 · → ↓ 다음 · Ctrl+휠 확대','muted'))
        split.addWidget(panel)
        split.handle(1).setToolTip('좌우로 드래그하여 감상 / 컨트롤 영역 크기 조절')
        self.right_split.handle(1).setToolTip('위아래로 드래그하여 태그 / 썸네일 영역 크기 조절')
        split.setSizes([960,420])
        self.right_split.setSizes([520,300])
        if self.settings is not None:
            for key,widget in [('panels/main',self.main_split),('panels/right',self.right_split)]:
                state=self.settings.value(key)
                if state is not None:widget.restoreState(state)
        self.main_split.splitterMoved.connect(self.save_panels)
        self.right_split.splitterMoved.connect(self.save_panels)
        self.statusBar().showMessage('원본 파일은 그대로 유지됩니다.')
        self.refresh_current()
    def save_panels(self,*args):
        if self.settings is not None:
            self.settings.setValue('panels/main',self.main_split.saveState())
            self.settings.setValue('panels/right',self.right_split.saveState())
            self.settings.sync()
    def tagging_targets(self):
        return sorted(self.batch_ids) if self.batch_mode.isChecked() else ([self.current_id] if self.current_id is not None else [])
    def batch_changed(self,enabled):
        self.batch_mode.setText('다중 선택 태깅 · '+('ON' if enabled else 'OFF'))
        self.batch_ids={self.current_id} if enabled and self.current_id is not None else set()
        self.thumbs.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection if enabled else QAbstractItemView.SelectionMode.SingleSelection)
        self.thumbs.selectionModel().blockSignals(True)
        self.thumbs.clearSelection()
        if self.thumbs.currentIndex().isValid():self.thumbs.selectionModel().select(self.thumbs.currentIndex(),QItemSelectionModel.SelectionFlag.Select)
        self.thumbs.selectionModel().blockSignals(False)
        self.batch_label.setVisible(enabled)
        self.batch_label.setText(f'선택 {len(self.batch_ids)}장 · 태그는 선택 사진 전체에 적용')
        self.refresh_current()
        self.render_tags()
    def batch_selection_changed(self,*args):
        if self.batch_mode.isChecked():
            self.batch_ids={self.rows[i.row()][0] for i in self.thumbs.selectionModel().selectedIndexes() if i.row()<len(self.rows)}
            self.batch_label.setText(f'선택 {len(self.batch_ids)}장 · 태그는 선택 사진 전체에 적용')
            self.refresh_current()
            self.render_tags()
    def apply_batch_tags(self,tag_id=None,enabled=True,names=None):
        targets=self.tagging_targets()
        if not targets:return False
        try:
            previous=self.library.edit_tags_many(targets,tag_id,enabled,names)
        except Exception as error:
            self.error(str(error));return False
        if previous:self.undo_edit=('batch',self.current_id,previous)
        self.refresh_results();self.render_tags()
        self.statusBar().showMessage(f'{len(targets)}장에 태그 변경을 저장했습니다.',2500)
        return True
    def choose_folder(self):
        root=QFileDialog.getExistingDirectory(self,'이미지 라이브러리 폴더 선택',str(self.library.root) if self.library else '')
        if root:
            self.open_folder(root)
    def restore_last_library(self):
        previous=self.settings.value('last_library','') if self.settings is not None else ''
        if not isinstance(previous,str) or not previous:
            return False
        if not Path(previous).is_dir():
            self.statusBar().showMessage('마지막 이미지 폴더에 접근할 수 없습니다. 드라이브를 연결하거나 폴더 열기로 위치를 지정하세요.')
            return False
        self.open_folder(previous)
        return True
    def open_folder(self,root):
        if self.scan and self.scan.isRunning():
            return
        self.open_button.setEnabled(False)
        self.open_action.setEnabled(False)
        self.rescan_action.setEnabled(False)
        self.statusBar().showMessage('하위 폴더의 이미지를 읽는 중…')
        self.scan=Scan(root,self)
        self.scan.result.connect(self.scan_done)
        self.scan.start()
    def scan_done(self,root,paths,error):
        self.open_button.setEnabled(True)
        self.open_action.setEnabled(True)
        self.rescan_action.setEnabled(True)
        if error:
            self.error('폴더를 모두 읽지 못했습니다. 기존 라이브러리는 유지됩니다.\n'+error)
            return
        new=None
        try:
            new=Library(root)
            if new.recovered_operation:
                paths=scan_folder(new.root)
            new.sync(paths)
        except Exception as err:
            if new:
                new.close()
            self.error('라이브러리를 저장할 수 없습니다. 폴더의 쓰기 권한과 데이터 파일을 확인하세요.\n'+str(err))
            return
        same=self.library and self.library.root==new.root
        if self.library:
            self.save_position()
            self.library.close()
        self.undo_edit=None
        self.batch_ids.clear()
        self.library=new
        self.saved_position=None
        # Signals from restoring controls must not save a partially restored state.
        self.restoring=True
        try:
            if not same:
                state=new.position()
                valid_tags={t[0] for t in new.tags()}
                self.filters=set(state['filters']) & valid_tags
                self.filter_origin=state.get('filter_origin') if self.filters else None
                controls=(self.sort,self.tag_tabs,self.search,self.image_sort,self.image_tabs)
                for control in controls:
                    control.blockSignals(True)
                try:
                    self.sort.setCurrentIndex(state['sort'])
                    self.tag_tabs.setCurrentIndex(state['tab'])
                    self.search.setText(state['search'])
                    self.image_sort.setCurrentIndex(state['image_sort'])
                    self.image_tabs.setCurrentIndex(1 if state.get('favorites_only') is True else 0)
                finally:
                    for control in controls:
                        control.blockSignals(False)
                self.current_id=state['image']
                self.active_tag=state['tag']
            self.viewer.path=None
            self.model.reset(new.root,[])
            self.folder_label.setText(new.root.name or str(new.root))
            self.folder_label.setToolTip(str(new.root))
            self.setWindowTitle(f'Luma {APP_VERSION} — {new.root.name}')
            self.refresh_results()
            self.render_tags()
        finally:
            self.restoring=False
        self.statusBar().showMessage(f'{len(paths):,}개 이미지 · 변경 내용은 자동 저장됩니다.')
        self.save_position()
        if self.settings is not None:
            self.settings.setValue('last_library',str(new.root))
            self.settings.sync()
        if self.settings is not None and self.settings.status()!=QSettings.Status.NoError:
            self.statusBar().showMessage('마지막 폴더 설정을 저장하지 못했습니다. 이미지와 태그 데이터는 유지됩니다.')
    def error(self,message):
        self.statusBar().showMessage('작업을 완료하지 못했습니다.')
        QMessageBox.warning(self,'Luma',message)
    def render_tags(self,*args):
        scroll=self.tag_list.verticalScrollBar().value()
        self.tag_list.blockSignals(True)
        self.tag_list.clear()
        tags=self.library.tags() if self.library else []
        names={tag_id:name for tag_id,name,count in tags}
        text=' + '.join(names[t] for t in sorted(self.filters) if t in names)
        self.filter_label.setText(text or '전체 이미지')
        self.filter_label.setToolTip(text)
        query=self.search.text().casefold()
        tags=[t for t in tags if query in t[1].casefold()]
        mode=self.sort.currentIndex()
        tags.sort(key=(lambda t:(t[1].casefold(),t[0])) if mode<2 else (lambda t:((-t[2] if mode==2 else t[2]),t[1])))
        if mode==1:
            tags.reverse()
        tab=self.tag_tabs.currentIndex()
        self.sort.setEnabled(tab==0)
        usage=self.library.usage() if self.library else {}
        if tab==1:
            tags=[t for t in tags if t[0] in usage]
            tags.sort(key=lambda t:(-usage[t[0]][1],t[1]))
        elif tab==2:
            tags.sort(key=lambda t:(-usage.get(t[0],(0,0))[0],-t[2],t[1]))
        # Search/tab visibility is not a command to select a different tag.
        if self.active_tag not in names:
            self.active_tag=None
        attached={t[0] for t in self.library.image_tags(self.current_id)} if self.library and self.current_id else set()
        targets=self.tagging_targets()
        target_tags=[{t for t,n in self.library.image_tags(i)} for i in targets] if self.library else []
        if self.batch_mode.isChecked():
            attached=set.intersection(*target_tags) if target_tags else set()
        partial=set.union(*target_tags)-attached if target_tags else set()
        for tag_id,name,count in tags:
            item=QListWidgetItem(self.tag_list)
            item.setData(Qt.ItemDataRole.UserRole,tag_id)
            widget=QWidget()
            row=QHBoxLayout(widget)
            row.setContentsMargins(4,0,4,0)
            checkbox=QCheckBox()
            checkbox.setChecked(tag_id in self.filters)
            checkbox.setToolTip(f'{name}: 감상 필터에 포함 / 해제')
            checkbox.clicked.connect(lambda checked,t=tag_id:self.toggle_filter(t,checked))
            row.addWidget(checkbox)
            short=name if len(name)<=9 else name[:8]+'…'
            label=self.button(f'{"✓ " if tag_id in attached else ""}{short} · {count:,}',lambda checked=False,t=tag_id:self.choose_tag(t))
            label.setToolTip(f'{name} · 이미지 {count:,}개\nWASD 선택 · E: 등록/해제 · F: 필터')
            label.setStyleSheet('text-align:left;padding:3px;color:'+('#b7d5ff' if tag_id in attached else '#d9dde5')+';')
            label.setMinimumWidth(0)
            row.addWidget(label,1)
            button=self.button('✓' if tag_id in attached else '−' if tag_id in partial else '+',lambda checked=False,t=tag_id:self.toggle_tag(t))
            button.setFixedSize(32,28)
            button.setEnabled(bool(targets))
            button.setToolTip(f'{name}: '+('선택 전체에서 해제' if tag_id in attached else '선택 전체에 추가') if self.batch_mode.isChecked() else f'{name} 등록 / 해제 · E')
            row.addWidget(button)
            item.setSizeHint(QSize(164,44))
            self.tag_list.setItemWidget(item,widget)
            if tag_id==self.active_tag:
                self.tag_list.setCurrentItem(item)
        self.tag_list.doItemsLayout()
        self.tag_list.verticalScrollBar().setValue(scroll)
        self.tag_list.blockSignals(False)
        self.update_active_label()
    def update_active_label(self):
        names={t[0]:t[1] for t in self.library.tags()} if self.library else {}
        name=names.get(self.active_tag,'없음')
        display=name if len(name)<26 else name[:23]+'…'
        attached=self.library and self.current_id is not None and self.active_tag in {t for t,n in self.library.image_tags(self.current_id)}
        hidden=self.active_tag is not None and not any(self.tag_list.item(i).data(Qt.ItemDataRole.UserRole)==self.active_tag for i in range(self.tag_list.count()))
        if self.batch_mode.isChecked():
            targets=self.tagging_targets()
            count=sum(self.active_tag in dict(self.library.image_tags(i)) for i in targets) if self.library else 0
            attached=bool(targets) and count==len(targets)
        flags=('현재 목록 밖 · ' if hidden else '')+('등록됨' if attached else '미등록')+' · '+('필터 ON' if self.active_tag in self.filters else '필터 OFF')
        if self.batch_mode.isChecked():flags=f'선택 {len(targets)}장 중 {count}장 등록 · '+('필터 ON' if self.active_tag in self.filters else '필터 OFF')
        self.active_label.setText(f'선택: {display} · {flags}\nWASD 선택 · E 등록/해제 · F 필터 · R 즐겨찾기')
        self.active_label.setToolTip(f'선택 태그: {name}\n{flags}\nCtrl+T: 검색 → Esc: 탐색 → WASD 선택 → E 등록\n입력 중 W/A/S/D/Q/E/F는 문자로 입력됩니다.')
        self.save_position()
    def tag_selected(self,current,previous):
        if current:
            self.active_tag=current.data(Qt.ItemDataRole.UserRole)
            self.update_active_label()
    def choose_tag(self,tag_id):
        self.active_tag=tag_id
        for i in range(self.tag_list.count()):
            item=self.tag_list.item(i)
            if item.data(Qt.ItemDataRole.UserRole)==tag_id:
                self.tag_list.setCurrentItem(item)
                break
        self.update_active_label()
        self.tag_list.setFocus()
    def navigate_tags(self,dx,dy):
        """Move spatially through the visible tag grid, never through images."""
        if not self.tag_list.count():
            return
        self.tag_list.doItemsLayout()
        current=self.tag_list.currentItem()
        if current is None:
            target=self.tag_list.item(0)
        else:
            origin=self.tag_list.visualItemRect(current).center()
            candidates=[]
            for i in range(self.tag_list.count()):
                item=self.tag_list.item(i)
                delta=self.tag_list.visualItemRect(item).center()-origin
                primary=delta.x()*dx if dx else delta.y()*dy
                cross=abs(delta.y()) if dx else abs(delta.x())
                if primary<=0:
                    continue
                # A/D stay in the same visual row. W/S use the closest next row,
                # preferring the same column, including an incomplete last row.
                if dx and cross>=self.tag_list.gridSize().height()/2:
                    continue
                candidates.append((primary,cross,i))
            target=self.tag_list.item(min(candidates)[2]) if candidates else current
        self.tag_list.setCurrentItem(target)
        self.active_tag=target.data(Qt.ItemDataRole.UserRole)
        self.tag_list.scrollToItem(target)
        self.tag_list.setFocus()
        self.update_active_label()
    def focus_search(self):
        self.search.setFocus()
        self.search.selectAll()
    def toggle_selected(self):
        self.toggle_tag(self.active_tag)
    def toggle_tag(self,tag_id,remove=False):
        if self.batch_mode.isChecked():
            targets=self.tagging_targets()
            if tag_id is None or not targets:return
            all_have=all(tag_id in dict(self.library.image_tags(i)) for i in targets)
            self.apply_batch_tags(tag_id,False if remove else not all_have)
            return
        if not self.library or self.current_id is None or tag_id is None:
            return
        image_id=self.current_id
        before=tag_id in {t for t,n in self.library.image_tags(image_id)}
        try:
            self.library.set_tag_links(image_id,{tag_id:not before})
        except Exception as error:
            self.error(str(error))
            return
        self.undo_edit=('tags',image_id,{tag_id:before})
        self.refresh_results()
        self.render_tags()
        self.viewer.setFocus()
    def toggle_favorite(self):
        if not self.library or self.current_id is None:
            return
        image_id=self.current_id
        before=image_id in self.library.favorites()
        try:
            self.library.set_favorite(image_id,not before)
        except Exception as error:
            self.error(str(error))
            return
        self.undo_edit=('favorite',image_id,before)
        self.refresh_results()
        self.render_tags()
        self.viewer.setFocus()
    def undo_last(self):
        if not self.undo_edit or not self.library:
            return
        kind,image_id,state=self.undo_edit
        try:
            if kind=='batch':
                self.library.edit_tags_many(list(state),restore=state)
            elif kind=='tags':
                self.library.set_tag_links(image_id,state)
            else:
                self.library.set_favorite(image_id,state)
        except Exception as error:
            self.error(str(error))
            return
        self.undo_edit=None
        self.current_id=image_id
        self.refresh_results()
        self.render_tags()
        self.statusBar().showMessage('직전 편집을 취소하고 저장했습니다.',2500)
    def rename_tag_dialog(self):
        if not self.library or self.active_tag is None:
            return
        name=next(n for t,n,c in self.library.tags() if t==self.active_tag)
        value,ok=QInputDialog.getText(self,'태그 이름 변경','새 태그 이름',text=name)
        if ok:
            try:
                self.library.rename_tag(self.active_tag,value)
            except Exception as error:
                self.error(str(error))
                return
            self.refresh_results()
            self.render_tags()
        self.viewer.setFocus()
    def delete_tag_dialog(self):
        if not self.library or self.active_tag is None:
            return
        tag_id=self.active_tag
        name=next(n for t,n,c in self.library.tags() if t==tag_id)
        if QMessageBox.question(self,'태그 자체 삭제',f'{name} 태그와 모든 이미지의 해당 연결을 삭제할까요?\n이미지 파일과 다른 태그는 유지됩니다.',QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,QMessageBox.StandardButton.No)!=QMessageBox.StandardButton.Yes:
            return
        try:
            self.library.delete_tag(tag_id)
        except Exception as error:
            self.error(str(error))
            return
        self.undo_edit=None
        self.active_tag=None
        self.set_filters(self.filters-{tag_id})
        self.viewer.setFocus()
    def set_filters(self,filters):
        filters=set(filters)
        if not self.filters and filters:
            self.filter_origin=self.current_id
        elif self.filters and not filters:
            candidates=self.library.results((),self.image_sort.currentIndex(),self.image_tabs.currentIndex()==1) if self.library else []
            if self.filter_origin in {i for i,p in candidates}:
                self.current_id=self.filter_origin
            self.filter_origin=None
        self.filters=filters
        self.refresh_results()
        self.render_tags()
    def toggle_filter(self,tag_id,checked):
        self.set_filters(self.filters|{tag_id} if checked else self.filters-{tag_id})
    def filter_changed(self,item):
        self.toggle_filter(item.data(Qt.ItemDataRole.UserRole),item.checkState()==Qt.CheckState.Checked)
    def clear_filters(self):
        self.set_filters(set())
    def save_position(self):
        if not self.library or self.restoring:
            return
        state=(self.current_id,tuple(sorted(self.filters)),self.active_tag,
               self.sort.currentIndex(),self.tag_tabs.currentIndex(),self.search.text(),self.image_sort.currentIndex(),self.image_tabs.currentIndex()==1,self.filter_origin)
        if state==self.saved_position:
            return
        try:
            self.library.save_position(self.current_id,self.filters,self.active_tag,
                self.sort.currentIndex(),self.tag_tabs.currentIndex(),self.search.text(),self.image_sort.currentIndex(),self.image_tabs.currentIndex()==1,self.filter_origin)
            self.saved_position=state
        except Exception as error:
            self.statusBar().showMessage('작업 위치 저장 실패: '+str(error))
    def toggle_bookmark(self):
        if self.current_id is None:
            return
        try:
            added=self.library.toggle_bookmark(self.current_id)
        except Exception as error:
            self.error(str(error))
            return
        self.refresh_current()
        self.statusBar().showMessage('북마크를 저장했습니다.' if added else '북마크를 해제했습니다.',2500)
    def jump_bookmark(self,image_id):
        if not self.library:
            return False
        if image_id not in {r[0] for r in self.library.results()}:
            self.statusBar().showMessage('이 북마크의 원본을 찾을 수 없습니다. 파일 위치를 확인한 뒤 F5로 다시 읽으세요.')
            return False
        cleared=image_id not in {r[0] for r in self.rows}
        if cleared:
            if image_id not in {i for i,p in self.library.results(self.filters)}:
                self.filters.clear()
                self.filter_origin=None
            if self.image_tabs.currentIndex()==1 and image_id not in self.library.favorites():
                self.image_tabs.blockSignals(True)
                self.image_tabs.setCurrentIndex(0)
                self.image_tabs.blockSignals(False)
        self.current_id=image_id
        self.refresh_results()
        self.render_tags()
        self.viewer.setFocus()
        if cleared:
            self.statusBar().showMessage('북마크로 이동하기 위해 감상 필터를 해제했습니다.',5000)
        return True
    def show_bookmarks(self):
        if not self.library:
            return
        dialog=QDialog(self)
        dialog.setWindowTitle('북마크로 이동')
        dialog.resize(540,440)
        layout=QVBoxLayout(dialog)
        search=QLineEdit()
        search.setPlaceholderText('북마크 파일 이름 또는 경로 찾기')
        listing=QListWidget()
        layout.addWidget(search)
        layout.addWidget(listing)
        layout.addWidget(QLabel('Enter 또는 더블클릭으로 이동 · 대상이 필터 밖이면 필터를 해제합니다.'))
        def fill():
            listing.clear()
            for image_id,path,present in self.library.bookmarks():
                if search.text().casefold() in path.casefold():
                    item=QListWidgetItem(('★ ' if present else '원본 없음 · ')+path,listing)
                    item.setData(Qt.ItemDataRole.UserRole,image_id)
            if listing.count():
                listing.setCurrentRow(0)
        def go(*args):
            item=listing.currentItem()
            if item and self.jump_bookmark(item.data(Qt.ItemDataRole.UserRole)):
                dialog.accept()
        search.textChanged.connect(fill)
        search.returnPressed.connect(go)
        listing.itemActivated.connect(go)
        layout.addWidget(self.button('선택 북마크로 이동',go))
        fill()
        dialog.exec()
    def refresh_results(self):
        previous_id=self.model.rows[self.thumbs.currentIndex().row()][0] if self.thumbs.currentIndex().isValid() and self.thumbs.currentIndex().row()<len(self.model.rows) else None
        scroll=self.thumbs.verticalScrollBar().value()
        rows=self.library.results(self.filters,self.image_sort.currentIndex(),self.image_tabs.currentIndex()==1) if self.library else []
        changed=rows!=self.model.rows or (self.library and self.model.root!=self.library.root)
        self.rows=rows
        ids=[r[0] for r in self.rows]
        self.batch_ids.intersection_update(ids)
        if self.current_id not in ids:
            self.current_id=ids[0] if ids else None
        if changed:
            self.thumbs.selectionModel().blockSignals(True)
            self.model.reset(self.library.root if self.library else Path('.'),self.rows)
            self.thumbs.selectionModel().blockSignals(False)
        self.select_current(reveal=self.current_id!=previous_id)
        if self.current_id==previous_id:
            self.thumbs.doItemsLayout()
            self.thumbs.verticalScrollBar().setValue(scroll)
    def select_current(self,reveal=True):
        row=next((i for i,r in enumerate(self.rows) if r[0]==self.current_id),-1)
        self.thumbs.selectionModel().blockSignals(True)
        if self.batch_mode.isChecked():
            self.thumbs.selectionModel().setCurrentIndex(self.model.index(row) if row>=0 else QModelIndex(),QItemSelectionModel.SelectionFlag.NoUpdate)
            self.thumbs.clearSelection()
            for i,(image_id,path) in enumerate(self.rows):
                if image_id in self.batch_ids:self.thumbs.selectionModel().select(self.model.index(i),QItemSelectionModel.SelectionFlag.Select)
            self.batch_label.setText(f'선택 {len(self.batch_ids)}장 · 태그는 선택 사진 전체에 적용')
        else:
            self.thumbs.setCurrentIndex(self.model.index(row) if row>=0 else QModelIndex())
        self.thumbs.selectionModel().blockSignals(False)
        if reveal and row>=0:
            self.thumbs.scrollTo(self.model.index(row))
        self.refresh_current()
    def image_sort_changed(self,*args):
        self.refresh_results()
        self.render_tags()
    def _prepare_file_edit(self):
        if self.current_id is None or not self.library:
            return False
        if self.scan and self.scan.isRunning():
            self.statusBar().showMessage('폴더 읽기가 끝난 뒤 파일을 편집하세요.')
            return False
        self.model.pool.clear()
        self.model.pool.waitForDone()
        self.model.pending.clear()
        self.file_edit_view=(self.viewer.zoom,QPointF(self.viewer.pan))
        self.viewer.show_path(None) # Release animated-image handles before editing.
        return True
    def restore_edit_view(self):
        self.viewer.zoom,self.viewer.pan=self.file_edit_view
        self.viewer.clamp_pan()
        self.viewer.request_detail()
        self.viewer.update()
    def rename_current(self):
        if self.current_id is None:
            return
        path=next(p for i,p in self.rows if i==self.current_id)
        name,accepted=QInputDialog.getText(self,'이미지 이름 변경','파일 이름 (확장자는 유지됩니다)',text=Path(path).name)
        if accepted:
            self.rename_to(name)
        self.viewer.setFocus()
    def rename_to(self,name):
        if not self._prepare_file_edit():
            return False
        try:
            self.library.rename_image(self.current_id,name)
        except Exception as error:
            self.refresh_results()
            self.restore_edit_view()
            self.error('이름을 변경하지 못했습니다.\n'+str(error))
            return False
        self.refresh_results()
        self.render_tags()
        self.viewer.setFocus()
        self.restore_edit_view()
        self.statusBar().showMessage('파일 이름을 변경했습니다. 태그와 북마크는 유지됩니다.',4000)
        return True
    def delete_current(self):
        if self.current_id is None:
            return
        path=next(p for i,p in self.rows if i==self.current_id)
        answer=QMessageBox.question(self,'원본 이미지 삭제',
            f'{path}\n\n이 원본 파일을 영구 삭제할까요?\n휴지통으로 이동하지 않습니다. 이 이미지의 태그 연결과 북마크도 제거됩니다.',
            QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,QMessageBox.StandardButton.No)
        if answer==QMessageBox.StandardButton.Yes:
            self.delete_selected_file()
        self.viewer.setFocus()
    def delete_selected_file(self):
        if not self._prepare_file_edit():
            return False
        index=next(i for i,r in enumerate(self.rows) if r[0]==self.current_id)
        successor=(self.rows[index+1][0] if index+1<len(self.rows) else self.rows[index-1][0] if index>0 else None)
        try:
            self.library.delete_image(self.current_id)
        except Exception as error:
            self.refresh_results()
            self.render_tags()
            self.restore_edit_view()
            self.error('파일 삭제를 완료하지 못했습니다.\n'+str(error))
            return False
        self.undo_edit=None
        self.current_id=successor
        self.refresh_results()
        self.render_tags()
        self.viewer.setFocus()
        self.statusBar().showMessage('원본 이미지와 해당 이미지의 연결 정보를 삭제했습니다.',4000)
        return True
    def refresh_current(self):
        row=next((i for i,r in enumerate(self.rows) if r[0]==self.current_id),-1)
        self.favorite_button.setEnabled(row>=0)
        self.favorite_button.setText('♥' if self.library and self.current_id in self.library.favorites() else '♡')
        self.current_tags.clear()
        self.tag_input.setEnabled(bool(self.tagging_targets()))
        self.add_button.setEnabled(bool(self.tagging_targets()))
        self.rename_action.setEnabled(row>=0)
        self.delete_action.setEnabled(row>=0)
        self.count_label.setText(f'{row+1 if row>=0 else 0} / {len(self.rows):,}개')
        if row<0:
            self.filename.setText('현재 이미지 없음')
            if self.library:
                self.viewer.show_path(None)
                if not self.filters and self.image_tabs.currentIndex()==0:
                    self.viewer.message='이 폴더에 지원되는 이미지가 없습니다.\n이미지를 추가한 뒤 F5로 다시 읽어 주세요.'
                    self.viewer.update()
            self.save_position()
            return
        path=self.rows[row][1]
        marked=self.current_id in {r[0] for r in self.library.bookmarks()}
        self.bookmark_action.setText('현재 이미지 북마크 해제' if marked else '현재 이미지 북마크 추가')
        self.filename.setText(('★ ' if marked else '')+(Path(path).name if len(Path(path).name)<42 else Path(path).name[:39]+'…'))
        self.filename.setToolTip(path)
        self.viewer.show_path(self.library.root/path)
        shown=dict(self.library.image_tags(self.current_id))
        if self.batch_mode.isChecked():
            shown={t:n for i in self.tagging_targets() for t,n in self.library.image_tags(i)}
        self.current_tags.setToolTip('클릭하면 선택 사진 전체에서 태그를 제거합니다.' if self.batch_mode.isChecked() else '클릭하면 현재 이미지에서 태그를 제거합니다.')
        for tag_id,name in sorted(shown.items(),key=lambda entry:entry[1]):
            item=QListWidgetItem(name+'  ×',self.current_tags)
            item.setData(Qt.ItemDataRole.UserRole,tag_id)
        self.save_position()
    def thumbnail_selected(self,index,previous):
        if index.isValid() and index.row()<len(self.rows):
            self.current_id=self.rows[index.row()][0]
            self.refresh_current()
            self.render_tags()
    def step(self,delta):
        if not self.rows:
            return
        row=next((i for i,r in enumerate(self.rows) if r[0]==self.current_id),0)
        self.current_id=self.rows[(row+delta)%len(self.rows)][0]
        self.select_current()
        self.render_tags()
    def add_input(self):
        if self.attach(self.tag_input.text().split(',')):
            self.tag_input.clear()
            self.viewer.setFocus()
    def attach(self,names):
        if self.batch_mode.isChecked():return self.apply_batch_tags(names=names)
        if self.current_id is None:
            return False
        try:
            before={t for t,n in self.library.image_tags(self.current_id)}
            self.library.attach(self.current_id,names)
            added={t for t,n in self.library.image_tags(self.current_id)}-before
            if added:
                self.undo_edit=('tags',self.current_id,{t:False for t in added})
        except Exception as error:
            self.error(str(error))
            return False
        self.refresh_results()
        self.render_tags()
        self.statusBar().showMessage('태그를 저장했습니다.',2500)
        return True
    def remove_tag(self,item):
        self.toggle_tag(item.data(Qt.ItemDataRole.UserRole),remove=True)
    def eventFilter(self,obj,event):
        if event.type()==QEvent.Type.KeyPress and self.isActiveWindow():
            focus=QApplication.focusWidget()
            if QApplication.activeModalWidget() is not None:
                return super().eventFilter(obj,event)
            # Arrow keys belong exclusively to image navigation in the main
            # workspace, even while a tag list, input, or combo box has focus.
            if event.key() in (Qt.Key.Key_Left,Qt.Key.Key_Up,Qt.Key.Key_Right,Qt.Key.Key_Down):
                self.step(-1 if event.key() in (Qt.Key.Key_Left,Qt.Key.Key_Up) else 1)
                return True
            if event.key()==Qt.Key.Key_Escape and not event.modifiers():
                self.viewer.setFocus()
                return True
            if event.modifiers()==Qt.KeyboardModifier.ControlModifier:
                if event.key() in (Qt.Key.Key_Return,Qt.Key.Key_Enter):
                    self.toggle_selected()
                    return True
                if event.key()==Qt.Key.Key_Z and not isinstance(focus,QLineEdit):
                    self.undo_last()
                    return True
            if focus==self.search and event.key() in (Qt.Key.Key_Return,Qt.Key.Key_Enter) and not event.modifiers():
                if self.tag_list.count():
                    self.toggle_selected()
                return True
            if not isinstance(focus,QLineEdit) and event.key()==Qt.Key.Key_Plus and event.modifiers()==Qt.KeyboardModifier.ShiftModifier:
                self.viewer.change_zoom(1.25)
                return True
            if not isinstance(focus,QLineEdit) and event.modifiers() in (Qt.KeyboardModifier.NoModifier,Qt.KeyboardModifier.ShiftModifier):
                directions={Qt.Key.Key_W:(0,-1),Qt.Key.Key_S:(0,1),Qt.Key.Key_A:(-1,0),Qt.Key.Key_D:(1,0)}
                if event.key() in directions:
                    self.navigate_tags(*directions[event.key()])
                    return True
                if event.key()==Qt.Key.Key_E or (focus==self.tag_list and event.key() in (Qt.Key.Key_Return,Qt.Key.Key_Enter)):
                    self.toggle_selected()
                    return True
                if event.key()==Qt.Key.Key_R:
                    self.toggle_favorite()
                    return True
                if event.key()==Qt.Key.Key_F and self.active_tag is not None:
                    self.toggle_filter(self.active_tag,self.active_tag not in self.filters)
                    return True
                if event.key() in (Qt.Key.Key_Plus,Qt.Key.Key_Equal):
                    self.viewer.change_zoom(1.25)
                    return True
                if event.key()==Qt.Key.Key_Minus:
                    self.viewer.change_zoom(0.8)
                    return True
                if event.key()==Qt.Key.Key_0:
                    self.viewer.reset_zoom()
                    return True
        return super().eventFilter(obj,event)
    def help(self):
        QMessageBox.information(self,f'Luma {APP_VERSION} 사용 방법',
            '일괄 태깅 모드: 일반 클릭으로 선택 추가/해제 (보조키 불필요). E/+는 전체 추가, 모두 등록되면 전체 해제.\n'
            '현재 태그 ×는 선택 전체에서 제거. Ctrl+Z로 직전 일괄 변경 취소.\n'
            '좌우 및 오른쪽 내부 경계를 드래그하면 패널 크기를 조절하고 기억합니다.\n\n'
            '1. 이미지 폴더를 엽니다. 모든 하위 폴더를 함께 읽습니다.\n'
            '2. ← / ↑는 이전 이미지, → / ↓는 다음 이미지입니다.\n'
            '3. 태그 입력 후 Enter. 쉼표로 여러 태그를 붙일 수 있습니다.\n'
            '4. 태그 목록의 +/✓ 또는 E는 태그 등록/해제 토글입니다.\n'
            '5. 체크한 태그를 모두 가진 이미지만 즉시 감상합니다.\n'
            '6. 현재 이미지의 태그 ×를 누르면 연결이 해제됩니다.\n\n'
            '감상 영역 아래 − / + / 화면 맞춤 / 100% 버튼을 사용하세요.\n'
            '편집 메뉴에서 선택 태그 이름 변경·태그 자체 삭제가 가능합니다.\n'
            '즐겨찾기 탭도 태그 필터와 이미지 정렬을 함께 적용합니다.\n'
            '확대: Ctrl+휠 또는 + / −. 드래그로 이동, 0 / 더블클릭으로 맞춤.\n'
            '다른 이미지로 넘기면 배율이 초기화됩니다. 태그 수정 중에는 유지됩니다.\n'
            'Ctrl+T: 태그 검색 → Esc: 입력 종료 → WASD: 태그 선택.\n'
            'W/S 위·아래, A/D 왼쪽·오른쪽 태그를 선택합니다.\n'
            'E: 선택 태그 등록/해제 · F: 필터 ON·OFF · R: 즐겨찾기.\n'
            '입력창에서는 WASD/Q/E/F를 문자로 입력하며 Esc로 탐색에 복귀합니다.\n'
            '메인 화면의 방향키는 입력창에서도 사진 이동 전용입니다.\n'
            'Ctrl+Enter: 태그 토글. Ctrl+Z: 직전 태그/즐겨찾기 편집 취소.\n'
            'Ctrl+B: 북마크 추가/해제. Ctrl+Shift+B: 북마크로 이동.\n'
            '최근/자주 사용 탭으로 반복 태깅할 태그를 빠르게 찾으세요.\n'
            '다시 실행하면 마지막 폴더·필터·정렬·이미지와 태그 탐색 상태를 복원합니다.\n\n'
            '태그는 소문자로 통일됩니다. 필터 해제는 태그를 지우지 않습니다.\n'
            'GIF / 애니메이션 WebP는 자동 재생됩니다.\n'
            '파일 변경 후 F5로 새로 읽으세요.\n\n'
            '썸네일 위 이미지 정렬: 이름 / 파일 크기 / 수정일. 다음 실행에 복원됩니다.\n'
            '편집 → F2: 원본 이름 변경 (태그·북마크 유지).\n'
            '편집 → Delete: 확인 후 원본 영구 삭제 (휴지통 이동 아님).\n\n'
            '데이터: 이미지 폴더 최상위 .luma-library.sqlite3\n'
            '감상·태깅은 원본을 변경하지 않습니다. 편집 메뉴는 원본에 적용됩니다.\n'
            '파일을 이동하거나 이름을 바꾸면 새 이미지로 인식합니다.\n'
            '라이브러리 폴더 전체를 옮기면 상대 경로와 태그는 유지됩니다.')
    def closeEvent(self,event):
        if self.scan and self.scan.isRunning():
            self.statusBar().showMessage('폴더 읽기가 끝난 뒤 종료해 주세요.')
            event.ignore()
            return
        self.save_panels()
        QApplication.instance().removeEventFilter(self)
        self.model.pool.clear()
        self.model.pool.waitForDone()
        if self.library:
            self.save_position()
            self.library.close()
        if self.settings is not None:
            self.settings.sync()
        if self.shell_identity:
            from windows_shell import clear_window
            clear_window(int(self.winId()))
            self.shell_identity=False
        event.accept()


def main():
    if os.name=='nt':
        from windows_shell import process_identity
        process_identity()
    app=QApplication(sys.argv)
    app.setApplicationName('Luma')
    app.setApplicationVersion(APP_VERSION)
    app.setWindowIcon(QIcon(str(Path(__file__).resolve().parent/'icon.ico')))
    app.setFont(QFont('Segoe UI',10))
    window=Window(remember=True)
    window.show()
    if len(sys.argv)>1:
        QTimer.singleShot(0,lambda:window.open_folder(sys.argv[1]))
    else:
        QTimer.singleShot(0,window.restore_last_library)
    sys.exit(app.exec())


if __name__=='__main__':
    main()
