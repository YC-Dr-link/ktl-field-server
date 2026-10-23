"""PR이 이슈와 연결됐는지 확인한다 (협업 사이트 자동 감지 · 병합 시 이슈 자동 닫힘의 기준).

통과: PR 본문에 `Closes #번호` (close · fix · resolve 계열) 또는 이슈 없는 공통 작업이면 `no-issue`.
템플릿 안내 주석(<!-- … -->) 안의 예시는 검사에서 뺀다 — 본문을 안 고쳐도 통과하던 문제를 막으려고.
"""
import os
import re
import sys

body = re.sub(r"<!--.*?-->", "", os.environ.get("PR_BODY") or "", flags=re.S)
KEYWORD = r"\b(close[sd]?|fix(e[sd])?|resolve[sd]?)"
if re.search(KEYWORD + r"\s+#\d+", body, re.I):
    print("pr-link: 이슈와 연결됨")
    sys.exit(0)
if re.search(r"\bno-issue\b", body, re.I):
    print("pr-link: 이슈 없는 공통 작업 (no-issue)")
    sys.exit(0)
spaced = re.search(KEYWORD + r"\s+#\s+(\d+)", body, re.I)
if spaced:
    n = spaced.group(3)
    print(f"""pr-link: 'Closes # {n}'처럼 # 뒤에 띄어쓰기가 있어요.

GitHub는 이 형식을 이슈 연결로 보지 않아서, 병합해도 이슈 #{n}이 닫히지 않아요.
PR 본문을 'Closes #{n}' (띄어쓰기 없이)로 고쳐 주세요. 본문을 고치면 이 검사가 다시 돌아요.""")
    sys.exit(1)
print("""pr-link: PR 본문에 연결된 이슈가 없습니다.

PR 본문(설명)에 아래 중 하나를 적어 주세요. 본문을 고치면 이 검사가 다시 돌아요.
  Closes #12        ← 이 PR이 끝내는 이슈 번호, # 바로 뒤에 띄어쓰기 없이 (병합되면 이슈가 자동으로 닫혀요)
  no-issue          ← 이슈 없는 공통 작업일 때만

<!-- --> 안내 주석 안에 적은 것은 세지 않아요.
협업 사이트의 작업 현황판과 PM 보고서가 이 연결로 진행 상황을 따라갑니다.""")
sys.exit(1)
