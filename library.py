"""Local library storage and explicit, recoverable original-file edits."""
import os
import sqlite3
import stat
import json
import time
import hashlib
import uuid
from pathlib import Path

EXTENSIONS = {'.gif', '.jpg', '.jpeg', '.webp', '.png', '.bmp'}
DB_NAME = '.luma-library.sqlite3'


def scan_folder(root):
    paths = []
    def is_link(path):
        # Windows junctions are reparse points, but Python 3.10 is_symlink
        # does not identify all junctions. Never traverse them recursively.
        info=os.lstat(path)
        return stat.S_ISLNK(info.st_mode) or bool(getattr(info,'st_file_attributes',0)&0x400)
    def fail(error):
        raise error
    for directory, dirs, files in os.walk(root, followlinks=False, onerror=fail):
        dirs[:] = [d for d in dirs if not is_link(os.path.join(directory, d))]
        for name in files:
            path = Path(directory) / name
            if path.suffix.lower() in EXTENSIONS and not is_link(path):
                paths.append(path.relative_to(root).as_posix())
    return sorted(paths, key=str.casefold)


class Library:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.db = sqlite3.connect(self.root / DB_NAME, timeout=5)
        self.db.execute('PRAGMA foreign_keys=ON')
        version = self.db.execute('PRAGMA user_version').fetchone()[0]
        if version not in (0, 1):
            self.db.close()
            raise ValueError('더 새로운 버전에서 만든 라이브러리입니다.')
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS images (
                id INTEGER PRIMARY KEY, path TEXT NOT NULL UNIQUE COLLATE NOCASE,
                present INTEGER NOT NULL DEFAULT 1);
            CREATE TABLE IF NOT EXISTS tags (
                id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE);
            CREATE TABLE IF NOT EXISTS image_tags (
                image_id INTEGER NOT NULL REFERENCES images(id),
                tag_id INTEGER NOT NULL REFERENCES tags(id),
                PRIMARY KEY(image_id, tag_id));
            CREATE INDEX IF NOT EXISTS tag_images ON image_tags(tag_id, image_id);
            PRAGMA user_version=1;
        ''')
        self._extend()
        self._file_stats={}
        self.recovered_operation=self._recover_file_operation()

    def core_digest(self):
        digest=hashlib.sha256()
        for table,order in [('images','id'),('tags','id'),('image_tags','image_id,tag_id')]:
            for row in self.db.execute(f'SELECT * FROM {table} ORDER BY {order}'):
                digest.update(json.dumps(row,ensure_ascii=False).encode('utf-8'))
        return digest.hexdigest()

    def _extend(self):
        required={'bookmarks','tag_usage','workspace_state','favorites'}
        existing={r[0] for r in self.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if required <= existing:
            return
        # A consistent SQLite backup, never a replacement of the live database.
        backup=self.root/(DB_NAME+'.before-features.bak')
        if not backup.exists():
            temporary=backup.with_name(backup.name+'.'+uuid.uuid4().hex+'.tmp')
            destination=sqlite3.connect(temporary)
            try:
                self.db.backup(destination)
                if destination.execute('PRAGMA integrity_check').fetchone()[0]!='ok':
                    raise ValueError('기존 데이터 백업 검증에 실패했습니다.')
            finally:
                destination.close()
            try:
                temporary.rename(backup)
            except FileExistsError:
                temporary.unlink()
        try:
            self.db.execute('BEGIN IMMEDIATE')
            before=self.core_digest()
            self.db.execute('''CREATE TABLE IF NOT EXISTS bookmarks (
                image_id INTEGER PRIMARY KEY REFERENCES images(id), created REAL NOT NULL)''')
            self.db.execute('''CREATE TABLE IF NOT EXISTS tag_usage (
                tag_id INTEGER PRIMARY KEY REFERENCES tags(id), uses INTEGER NOT NULL DEFAULT 0,
                last_used REAL NOT NULL DEFAULT 0)''')
            self.db.execute('''CREATE TABLE IF NOT EXISTS workspace_state (
                key TEXT PRIMARY KEY, value TEXT NOT NULL)''')
            self.db.execute('CREATE TABLE IF NOT EXISTS favorites (image_id INTEGER PRIMARY KEY REFERENCES images(id))')
            if self.core_digest()!=before:
                raise ValueError('기존 데이터 보존 검사에 실패했습니다. 변경을 취소합니다.')
            self.db.commit()
        except Exception:
            self.db.rollback()
            self.db.close()
            raise

    def usage(self):
        return {tag_id:(uses,last) for tag_id,uses,last in self.db.execute('SELECT tag_id,uses,last_used FROM tag_usage')}

    def _used(self,tag_id):
        self.db.execute('''INSERT INTO tag_usage VALUES (?,1,?) ON CONFLICT(tag_id)
            DO UPDATE SET uses=uses+1,last_used=excluded.last_used''',(tag_id,time.time()))

    def bookmarks(self):
        return self.db.execute('''SELECT i.id,i.path,i.present FROM bookmarks b
            JOIN images i ON i.id=b.image_id ORDER BY b.created DESC''').fetchall()

    def toggle_bookmark(self,image_id):
        with self.db:
            exists=self.db.execute('SELECT 1 FROM bookmarks WHERE image_id=?',(image_id,)).fetchone()
            if exists:
                self.db.execute('DELETE FROM bookmarks WHERE image_id=?',(image_id,))
            else:
                self.db.execute('INSERT INTO bookmarks VALUES (?,?)',(image_id,time.time()))
        return not bool(exists)

    def save_position(self,image_id,filters,tag_id,sort_mode=None,tag_tab=None,tag_search=None,image_sort=None,favorites_only=None,filter_origin=None):
        # Extend the existing state record; never rewrite image/tag/bookmark data.
        state=self.position()
        state.update({'state_version':2,'image':image_id,'filters':sorted(filters),'tag':tag_id})
        if sort_mode is not None:
            state['sort']=sort_mode
        if tag_tab is not None:
            state['tab']=tag_tab
        if tag_search is not None:
            state['search']=tag_search
        if image_sort is not None:
            state['image_sort']=image_sort
        if favorites_only is not None:
            state['favorites_only']=bool(favorites_only)
        state['filter_origin']=filter_origin
        value=json.dumps(state,ensure_ascii=False)
        with self.db:
            self.db.execute('''INSERT INTO workspace_state VALUES ('position',?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value''',(value,))

    def position(self):
        row=self.db.execute("SELECT value FROM workspace_state WHERE key='position'").fetchone()
        try:
            value=json.loads(row[0]) if row else {}
        except (ValueError,TypeError):
            value={}
        if not isinstance(value,dict):
            value={}
        def identifier(item):
            return item if type(item) is int and item>0 else None
        value['image']=identifier(value.get('image'))
        value['tag']=identifier(value.get('tag'))
        value['filter_origin']=identifier(value.get('filter_origin'))
        filters=value.get('filters',[])
        value['filters']=sorted({t for t in filters if identifier(t) is not None}) if isinstance(filters,list) else []
        value['sort']=value.get('sort') if type(value.get('sort')) is int and value['sort'] in range(4) else 0
        value['tab']=value.get('tab') if type(value.get('tab')) is int and value['tab'] in range(3) else 0
        value['search']=value.get('search') if isinstance(value.get('search'),str) else ''
        value['image_sort']=value.get('image_sort') if type(value.get('image_sort')) is int and value['image_sort'] in range(6) else 0
        return value

    def sync(self, paths):
        self._file_stats.clear()
        with self.db:
            self.db.execute('UPDATE images SET present=0')
            self.db.executemany('''INSERT INTO images(path,present) VALUES (?,1)
                ON CONFLICT(path) DO UPDATE SET present=1''', ((p,) for p in paths))

    def results(self, filters=(),image_sort=0,favorites_only=False):
        if not filters:
            rows=self.db.execute('SELECT id,path FROM images WHERE present=1').fetchall()
        else:
            placeholders = ','.join('?' for _ in filters)
            rows=self.db.execute(f'''SELECT i.id,i.path FROM images i
                JOIN image_tags it ON it.image_id=i.id WHERE i.present=1 AND it.tag_id IN ({placeholders})
                GROUP BY i.id HAVING COUNT(DISTINCT it.tag_id)=?''', (*filters, len(filters))).fetchall()
        if favorites_only:
            favorites=self.favorites()
            rows=[r for r in rows if r[0] in favorites]
        name=lambda row:(Path(row[1]).name.casefold(),row[1].casefold(),row[0])
        rows.sort(key=name)
        if image_sort<2:
            return rows if image_sort==0 else list(reversed(rows))
        present,missing=[],[]
        for row in rows:
            try:
                if row[1] not in self._file_stats:
                    info=(self.root/row[1]).stat()
                    self._file_stats[row[1]]=(info.st_size,info.st_mtime_ns)
                present.append(row)
            except OSError:
                missing.append(row)
        column=0 if image_sort in (2,3) else 1
        present.sort(key=lambda row:self._file_stats[row[1]][column],reverse=image_sort in (3,5))
        return present+missing

    def _safe_path(self,relative):
        relative=Path(relative)
        if relative.is_absolute() or '..' in relative.parts or ':' in str(relative):
            raise ValueError('라이브러리 밖의 파일은 편집할 수 없습니다.')
        path=self.root/relative
        path.resolve().relative_to(self.root)
        if path.is_symlink():
            raise ValueError('링크 파일은 편집할 수 없습니다.')
        return path

    def _operation(self):
        row=self.db.execute("SELECT value FROM workspace_state WHERE key='file_operation'").fetchone()
        return json.loads(row[0]) if row else None

    def _record_operation(self,operation):
        with self.db:
            if self._operation():
                raise RuntimeError('이전 파일 작업을 복구한 뒤 다시 시도하세요.')
            self.db.execute("INSERT INTO workspace_state VALUES ('file_operation',?)",(json.dumps(operation),))

    def _clear_operation(self):
        with self.db:
            self.db.execute("DELETE FROM workspace_state WHERE key='file_operation'")

    def _delete_records(self,image_id):
        self.db.execute('DELETE FROM image_tags WHERE image_id=?',(image_id,))
        self.db.execute('DELETE FROM bookmarks WHERE image_id=?',(image_id,))
        self.db.execute('DELETE FROM favorites WHERE image_id=?',(image_id,))
        self.db.execute('DELETE FROM images WHERE id=?',(image_id,))

    def _restore_deleted(self,operation):
        with self.db:
            self.db.execute('''INSERT INTO images(id,path,present) VALUES (?,?,?)
                ON CONFLICT(id) DO UPDATE SET path=excluded.path,present=excluded.present''',operation['image'])
            self.db.executemany('INSERT OR IGNORE INTO image_tags VALUES (?,?)',operation['links'])
            self.db.executemany('INSERT OR REPLACE INTO bookmarks VALUES (?,?)',operation['bookmarks'])
            self.db.executemany('INSERT OR IGNORE INTO favorites VALUES (?)',operation.get('favorites',[]))
            self.db.execute("DELETE FROM workspace_state WHERE key='file_operation'")

    def _recover_file_operation(self):
        operation=self._operation()
        if not operation:
            return False
        source=self._safe_path(operation['image'][1])
        staged=self._safe_path(operation['staged'])
        if operation['kind']=='rename':
            target=self._safe_path(operation['target'])
            if staged.exists():
                if target.exists():
                    raise FileExistsError('파일 작업 복구 대상이 이미 존재합니다: '+str(target))
                staged.rename(target)
            elif source.exists() and not target.exists():
                self._clear_operation() # Intent persisted, original was not moved.
                return True
            elif source.exists() and target.exists():
                if not os.path.samefile(source,target):
                    raise FileExistsError('이름 변경 복구 경로에 서로 다른 파일이 있습니다.')
                actual=next((p.name for p in source.parent.iterdir() if p.name.casefold()==target.name.casefold()),None)
                if actual!=target.name:
                    self._clear_operation()
                    return True
            if not target.exists():
                raise FileNotFoundError('이름 변경 복구 파일을 찾을 수 없습니다.')
            with self.db:
                self.db.execute('UPDATE images SET path=? WHERE id=?',(operation['target'],operation['image'][0]))
                self.db.execute("DELETE FROM workspace_state WHERE key='file_operation'")
        elif operation['kind']=='delete':
            if source.exists():
                if staged.exists():
                    raise FileExistsError('삭제 복구 경로에 다른 파일이 있습니다.')
                self._restore_deleted(operation)
            else:
                try:
                    if staged.exists():
                        staged.unlink()
                except OSError:
                    staged.rename(source)
                    self._restore_deleted(operation)
                    return True
                with self.db:
                    self._delete_records(operation['image'][0])
                    self.db.execute("DELETE FROM workspace_state WHERE key='file_operation'")
        else:
            raise ValueError('알 수 없는 파일 작업 기록입니다.')
        return True

    def _edit_record(self,image_id):
        if self._operation():
            raise RuntimeError('이전 파일 작업 기록이 남아 있습니다. 라이브러리를 다시 열어 복구하세요.')
        row=self.db.execute('SELECT id,path,present FROM images WHERE id=? AND present=1',(image_id,)).fetchone()
        if row is None:
            raise ValueError('현재 이미지를 찾을 수 없습니다.')
        path=self._safe_path(row[1])
        if not path.is_file():
            raise FileNotFoundError('원본 파일을 찾을 수 없습니다. F5로 다시 읽어 주세요.')
        return row,path

    def rename_image(self,image_id,name):
        row,source=self._edit_record(image_id)
        if not name or name!=name.strip() or name.endswith('.') or any(c in '<>:"/\\|?*' or ord(c)<32 for c in name):
            raise ValueError('사용할 수 없는 파일 이름입니다. 경로 구분자나 끝의 공백·점은 사용할 수 없습니다.')
        reserved={'CON','PRN','AUX','NUL',*[f'COM{i}' for i in range(1,10)],*[f'LPT{i}' for i in range(1,10)]}
        if name.split('.')[0].upper() in reserved:
            raise ValueError('Windows 예약 이름은 사용할 수 없습니다.')
        if Path(name).suffix.lower()!=source.suffix.lower():
            raise ValueError('이미지 형식 보존을 위해 기존 확장자를 유지하세요.')
        if name==source.name:
            return row[1]
        target=source.with_name(name)
        relative=target.relative_to(self.root).as_posix()
        if target.exists() and not os.path.samefile(source,target):
            raise FileExistsError('같은 이름의 파일이 이미 있습니다.')
        if self.db.execute('SELECT id FROM images WHERE path=? AND id<>?',(relative,image_id)).fetchone():
            raise FileExistsError('다른 이미지의 기존 경로와 겹칩니다.')
        staged=source.with_name('.luma-edit-'+uuid.uuid4().hex+'.tmp')
        operation={'kind':'rename','image':row,'target':relative,'staged':staged.relative_to(self.root).as_posix()}
        self._record_operation(operation)
        try:
            source.rename(staged)
            staged.rename(target)
            with self.db:
                self.db.execute('UPDATE images SET path=? WHERE id=?',(relative,image_id))
                self.db.execute("DELETE FROM workspace_state WHERE key='file_operation'")
        except Exception:
            if staged.exists():
                staged.rename(source)
            elif target.exists() and not source.exists():
                target.rename(source)
            elif target.exists() and name.casefold()==source.name.casefold():
                target.rename(staged)
                staged.rename(source)
            self._clear_operation()
            raise
        self._file_stats.pop(row[1],None)
        return relative

    def delete_image(self,image_id):
        row,source=self._edit_record(image_id)
        staged=source.with_name('.luma-edit-'+uuid.uuid4().hex+'.tmp')
        operation={'kind':'delete','image':row,'staged':staged.relative_to(self.root).as_posix(),
            'favorites':self.db.execute('SELECT image_id FROM favorites WHERE image_id=?',(image_id,)).fetchall(),
            'links':self.db.execute('SELECT image_id,tag_id FROM image_tags WHERE image_id=?',(image_id,)).fetchall(),
            'bookmarks':self.db.execute('SELECT image_id,created FROM bookmarks WHERE image_id=?',(image_id,)).fetchall()}
        self._record_operation(operation)
        try:
            source.rename(staged)
            with self.db:
                self._delete_records(image_id)
        except Exception:
            if staged.exists():
                staged.rename(source)
            self._clear_operation()
            raise
        try:
            staged.unlink()
        except OSError:
            staged.rename(source)
            self._restore_deleted(operation)
            raise
        # Retain the recovery intent if this final metadata commit fails.
        self._clear_operation()
        self._file_stats.pop(row[1],None)

    def tags(self):
        return self.db.execute('''SELECT t.id,t.name,COUNT(i.id) FROM tags t
            LEFT JOIN image_tags it ON it.tag_id=t.id
            LEFT JOIN images i ON i.id=it.image_id AND i.present=1 GROUP BY t.id''').fetchall()

    def image_tags(self, image_id):
        return self.db.execute('''SELECT t.id,t.name FROM tags t JOIN image_tags it
            ON it.tag_id=t.id WHERE it.image_id=? ORDER BY t.name''', (image_id,)).fetchall()

    def attach(self, image_id, names):
        with self.db:
            for name in names:
                name = name.strip().casefold()
                if not name:
                    continue
                if len(name) > 100:
                    raise ValueError('태그는 100자 이내로 입력하세요.')
                self.db.execute('INSERT OR IGNORE INTO tags(name) VALUES (?)', (name,))
                tag_id=self.db.execute('SELECT id FROM tags WHERE name=?',(name,)).fetchone()[0]
                cursor=self.db.execute('INSERT OR IGNORE INTO image_tags VALUES (?,?)',(image_id,tag_id))
                if cursor.rowcount:
                    self._used(tag_id)

    def detach(self, image_id, tag_id):
        with self.db:
            cursor=self.db.execute('DELETE FROM image_tags WHERE image_id=? AND tag_id=?', (image_id, tag_id))
            if cursor.rowcount:
                self._used(tag_id)

    def favorites(self):
        return {r[0] for r in self.db.execute('SELECT image_id FROM favorites')}

    def set_favorite(self,image_id,enabled):
        with self.db:
            if enabled:
                self.db.execute('INSERT OR IGNORE INTO favorites VALUES (?)',(image_id,))
            else:
                self.db.execute('DELETE FROM favorites WHERE image_id=?',(image_id,))

    def set_tag_links(self,image_id,states):
        with self.db:
            for tag_id,enabled in states.items():
                if enabled:
                    cursor=self.db.execute('INSERT OR IGNORE INTO image_tags VALUES (?,?)',(image_id,tag_id))
                else:
                    cursor=self.db.execute('DELETE FROM image_tags WHERE image_id=? AND tag_id=?',(image_id,tag_id))
                if cursor.rowcount:
                    self._used(tag_id)

    def edit_tags_many(self,image_ids,tag_id=None,enabled=True,names=None,restore=None):
        """One transaction for every selected image; return exact previous links."""
        previous={}
        with self.db:
            tag_ids=[]
            if names is not None:
                for name in names:
                    name=name.strip().casefold()
                    if not name:continue
                    if len(name)>100:raise ValueError('태그는 100자 이내로 입력하세요.')
                    self.db.execute('INSERT OR IGNORE INTO tags(name) VALUES (?)',(name,))
                    tag_ids.append(self.db.execute('SELECT id FROM tags WHERE name=?',(name,)).fetchone()[0])
            elif tag_id is not None:tag_ids=[tag_id]
            for image_id in image_ids:
                states=restore[image_id] if restore is not None else {t:enabled for t in tag_ids}
                for t,value in states.items():
                    old=bool(self.db.execute('SELECT 1 FROM image_tags WHERE image_id=? AND tag_id=?',(image_id,t)).fetchone())
                    if old==value:continue
                    previous.setdefault(image_id,{})[t]=old
                    if value:self.db.execute('INSERT INTO image_tags VALUES (?,?)',(image_id,t))
                    else:self.db.execute('DELETE FROM image_tags WHERE image_id=? AND tag_id=?',(image_id,t))
                    self._used(t)
        return previous

    def rename_tag(self,tag_id,name):
        name=name.strip().casefold()
        if not name or len(name)>100:
            raise ValueError('태그 이름은 1~100자로 입력하세요.')
        if self.db.execute('SELECT 1 FROM tags WHERE name=? AND id<>?',(name,tag_id)).fetchone():
            raise ValueError('같은 이름의 태그가 이미 있습니다.')
        with self.db:
            self.db.execute('UPDATE tags SET name=? WHERE id=?',(name,tag_id))

    def delete_tag(self,tag_id):
        with self.db:
            self.db.execute('DELETE FROM image_tags WHERE tag_id=?',(tag_id,))
            self.db.execute('DELETE FROM tag_usage WHERE tag_id=?',(tag_id,))
            self.db.execute('DELETE FROM tags WHERE id=?',(tag_id,))
            state=self.position()
            state['filters']=[t for t in state['filters'] if t!=tag_id]
            if state['tag']==tag_id:
                state['tag']=None
            self.db.execute("UPDATE workspace_state SET value=? WHERE key='position'",(json.dumps(state,ensure_ascii=False),))

    def close(self):
        self.db.close()
