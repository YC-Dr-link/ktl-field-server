"""PR이 이슈와 연결됐는지 확인한다 (협업 사이트 자동 감지 · 병합 시 이슈 자동 닫힘의 기준).

통과: PR 본문에 `Closes #번호` (close · fix · resolve 계열) 또는 이슈 없는 공통 작업이면 `no-issue`.
"""
import os
import re
import sys

body = os.environ.get("PR_BODY") or ""
if re.search(r"\b(close[sd]?|fix(e[sd])?|resolve[sd]?)\s+#\d+", body, re.I):
    print("pr-link: 이슈와 연결됨")
    sys.exit(0)
if re.search(r"\bno-issue\b", body, re.I):
    print("pr-link: 이슈 없는 공통 작업 (no-issue)")
    sys.exit(0)
print("""pr-link: PR 본문에 연결된 이슈가 없습니다.

PR 본문(설명)에 아래 중 하나를 적어 주세요. 본문을 고치면 이 검사가 다시 돌아요.
  Closes #12        ← 이 PR이 끝내는 이슈 번호 (병합되면 이슈가 자동으로 닫혀요)
  no-issue          ← 이슈 없는 공통 작업일 때만

협업 사이트의 작업 현황판과 PM 보고서가 이 연결로 진행 상황을 따라갑니다.""")
sys.exit(1)
