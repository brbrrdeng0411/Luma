# Luma v1.0.0 — GitHub 직접 업로드 가이드

## 0. 먼저 구분할 것
- **Repository:** 소스코드와 README, 변경 이력을 보관하는 곳
- **Commit:** 특정 시점의 변경 기록
- **Push:** 로컬 Git 기록을 GitHub에 업로드
- **Release:** `v1.0.0` 같은 배포 버전과 패치노트, 실행 파일을 묶어 공개하는 곳

## 1. 준비
1. GitHub 계정을 만들고 **GitHub Desktop**을 설치하여 로그인합니다.
2. Luma 프로젝트의 **소스코드 폴더**를 확인합니다. 개인 사진 라이브러리 폴더와 혼동하지 마십시오.
3. 이 묶음에서 `README.md`, `README.en.md`, `README.ja.md`, `README.zh-CN.md`, `CHANGELOG.md`, `.gitignore`를 프로젝트 최상위 폴더에 복사합니다. 필요한 문구는 현재 구현 상태에 맞게 조정합니다.
4. `.gitignore`가 실제 프로젝트의 빌드·데이터 구조와 맞는지 확인합니다. 사용자 사진 폴더가 프로젝트 아래에 있다면 해당 폴더 경로를 `.gitignore`에 개별 추가합니다.

## 2. 처음 GitHub에 올리기: GitHub Desktop
1. GitHub Desktop에서 **File → Add local repository...**를 누르고 프로젝트 폴더를 선택합니다.
2. 아직 Git 저장소가 아니라는 안내가 표시되면, 같은 폴더에서 **Create a repository**를 선택합니다.
3. **Changes**에서 업로드 목록을 확인합니다. 개인 사진, 로컬 DB, 비밀번호, 불필요한 빌드 출력물이 포함되지 않았는지 확인합니다.
4. Commit 메시지에 `Initial release v1.0.0`을 입력하고 **Commit to main**을 누릅니다.
5. **Publish repository**를 누릅니다. 이름은 `Luma`로 정합니다.
6. 처음 연습하는 저장소를 외부에 보이지 않게 하려면 **Keep this code private**를 선택합니다. 공개하려면 Public으로 설정합니다.
7. **Publish repository**를 완료하면 GitHub 저장소에 파일과 README가 표시됩니다.

## 3. 저장소 소개란 작성
1. GitHub 저장소 우측 **About**의 편집 메뉴(톱니바퀴)를 엽니다.
2. `GITHUB_ABOUT.txt`에서 English 문구를 Description으로 넣습니다. GitHub의 Description 입력칸은 하나입니다.
3. Topics는 필요한 항목만 입력합니다.
4. 한국어·영어·일본어·중국어 본문 소개는 각 언어 README에 들어 있습니다.

## 4. `v1.0.0` Release 만들기
1. GitHub 저장소의 **Releases → Create a new release**를 엽니다.
2. 태그 선택에서 **Create new tag**로 `v1.0.0`을 만들고 대상 브랜치는 `main`으로 둡니다.
3. Release title: `Luma 1.0.0`.
4. 설명란에 `RELEASE_NOTES_v1.0.0.md` 내용을 붙여 넣습니다.
5. 테스트한 Windows 실행 파일 또는 압축 배포본이 있다면 **Assets**에 첨부합니다. 앱 실행 파일을 배포하지 않는 경우 소스코드만 공개된 상태입니다.
6. **Publish release**를 누릅니다.

## 5. 다음 패치 `v1.0.1`
1. 코드 변경과 기능 확인을 마칩니다.
2. `CHANGELOG.md` 맨 위에 `1.0.1`의 실제 변경 사항을 적습니다.
3. GitHub Desktop에서 **Commit → Push origin**을 수행합니다.
4. GitHub Releases에서 태그 `v1.0.1`, 제목 `Luma 1.0.1`로 새 Release를 만들고 실제 변경 사항을 적습니다.
5. 새 버전의 실행 파일 또는 배포 압축파일을 첨부합니다.

## 중요한 구분
- **Commit**과 **Release**는 다릅니다. 코드가 바뀔 때마다 Commit할 수 있고, 배포할 때 Release를 만들면 됩니다.
- 테스트 단계라면 **Private** 저장소도 괜찮습니다. Private Release는 접근 권한이 있는 사용자만 볼 수 있습니다.
- 배포용 `.exe`나 압축파일은 **Release Assets**로 업로드합니다. 큰 실행 파일을 Git 소스 이력에 계속 넣을 필요는 없습니다.
- `.gitignore`는 아직 Git에 등록하지 않은 파일에 적용됩니다. 이미 Commit한 민감한 파일은 `.gitignore`에 적는 것만으로 Git 기록에서 사라지지 않습니다.
- README에 적힌 기능이 현재 실제 동작하는지 확인한 뒤 최초 공개판으로 사용하십시오.
