"""exe 진입점.

PyInstaller 는 진입 스크립트를 패키지가 아닌 최상위 모듈(__main__)로 실행하므로
`from .app import main` 같은 상대 임포트를 쓸 수 없다. 절대 임포트로 본체를 부른다.
(`python -m what_number` 로 실행할 때는 src/what_number/__main__.py 가 쓰인다.)
"""

import sys

from what_number.app import main

if __name__ == "__main__":
    sys.exit(main())
