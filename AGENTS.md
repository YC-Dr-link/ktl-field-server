# AGENTS.md — ktl-field-server

> 코딩 Agent(Claude Code · Cursor · Codex 등)가 이 레포에서 일할 때 지키는 규칙. 사람도 같이 본다.

## 이 레포
- 파트: **B-1 현장 서버 (백엔드)** · 담당 여준(@Agheel)
- 무엇을 만드나: 요구사항 REQ-B1-01~14 — https://raw.githubusercontent.com/YC-Dr-link/ktl-hub/main/docs/01-요구사항명세.md
- 파트 정보 · 제출물 형태: https://raw.githubusercontent.com/YC-Dr-link/ktl-hub/main/docs/04-역할분담.md
- 기술: Python 3.11+ · FastAPI · SQLAlchemy 2 + Alembic · SQLite(WAL) · 작업 큐는 multiprocessing(외부 큐 서버 없이)

## 계약 (바꾸지 말 것)
API는 https://raw.githubusercontent.com/YC-Dr-link/ktl-hub/main/docs/api/field-server.openapi.yaml 이 기준이다 (사람용 설명 02 §1~2). 모듈은 ktl_contract(02 §3)로만 부른다.
계약과 다르게 해야 할 것 같으면 **멈추고 사람에게 보고**한다. 사람이 ktl-hub에 `계약변경` 이슈를 연다.
- API · 모듈 계약 · QR · 출력 형식: https://raw.githubusercontent.com/YC-Dr-link/ktl-hub/main/docs/02-API명세.md
- 화면 시나리오: https://raw.githubusercontent.com/YC-Dr-link/ktl-hub/main/docs/03-UI시나리오.md

## 일하는 방식
1. **이슈 하나만** 한다. 사람이 준 이슈 번호(또는 협업 사이트의 📋 작업 지시서)를 기준으로 범위를 정한다. 다른 파트 코드는 건드리지 않는다.
2. 브랜치에서 일한다: `git checkout main && git pull && git checkout -b feat/<이슈번호>-<짧은설명>`. main에 직접 push하지 않는다.
3. 작게 커밋한다. 메시지: `feat: 무엇을 (#이슈번호)` (feat · fix · docs · test · refactor · chore). 커밋 · PR에 AI 서명 줄(Co-Authored-By 등)을 넣지 않는다.
4. 테스트를 같이 쓴다: pytest — API 응답이 OpenAPI와 같은지, 가짜 모듈(ktl_contract.fakes)로 업로드 → 판독 → 확정 → 내보내기가 끝까지 되는지
5. README의 실행 방법(3줄 이내)을 맞춘다.
6. 끝나면 사람에게 보고한다: 바뀐 파일, 테스트 결과, `TODO(확인 필요)` 목록, PR 본문 초안(`Closes #이슈번호` 포함). PR은 400줄 안쪽이 좋다. 크면 나눈다.

## 하지 말 것
- **실제 견본 데이터(CSV · 사진 · 엑셀 · txt), 실명, 업체명, 연구원 이름을 커밋하지 않는다.** 테스트 파일은 가짜 값으로 만들어 `synthetic/`에만. CI `data-guard`가 막는다.
- 제품 코드에서 인터넷 호출(외부 API · CDN · 폰트 · 자동 업데이트) 금지. 현장은 오프라인이다.
- KTL 내부 규정 · 시스템 · 장비 · 실제 데이터 형식처럼 문서에 없는 것은 **추측하지 않는다.** 코드에 `TODO(확인 필요): …`로 남기고 보고한다.
- 병합하지 않는다. 병합은 PM만 한다.

## 이 파트에서 특히
- 휴대폰 API(:8765)와 관리 API(127.0.0.1:8766)를 분리한다.
- 원본 사진·파일은 읽기 전용으로 보관하고 SHA-256을 남긴다.
- 모듈이 예외를 내거나 10초 넘게 멈춰도 서버는 계속 돌아야 한다 (그 사진만 failed).
- 처음에는 ktl_contract.fakes의 가짜 모듈로 끝까지 동작시키는 것이 목표(관문 2).

## 일정
관문 1 10/25 · 관문 2 11/1(가짜 모듈로 끝까지) · 관문 3 11/6(기능 완성 · 코드 동결) · 리허설 11/8 — https://raw.githubusercontent.com/YC-Dr-link/ktl-hub/main/docs/06-일정.md
