"""정적→동적 검증 엔진 — 정적 taint finding 을 샌드박스에서 기동한 앱에 무해 마커로 실제
요청해 confirmed/false_positive 로 판정한다(화이트박스 DAST). 이번 세션의 SQL 인젝션 오탐
9건(전부 파라미터 바인딩 쿼리) 제거가 1차 목표.

모듈:
- entrypoint: finding(sink) → HTTP 라우트 해석(코드 그래프 + 프레임워크 규칙)
- probes:     무해 프로브 합성(안전 템플릿 라이브러리, 파괴 금지)
- oracle:     응답·로그·IAST 신호 → verdict(순수 함수)
- (phase-2) runner/feedback/instrumentation: 실제 HTTP 실행·판정 반영·IAST-lite
"""
