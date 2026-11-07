# ADR-0001 4계층 + Ports & Adapters

- 상태: 채택 (2026-11-07)

## 맥락
과제 문서는 Presentation / Application / Persistence / Domain 4계층과 "AI 모델은 Adapter 를 통해서만 호출" 을 요구한다.
동시에 구조화·생성·추적·검수 모델을 실험하며 바꿔야 하고, 팀은 2명이라 변경 비용이 커지면 안 된다.

## 결정
4계층 이름과 책임은 그대로 두되, Application 이 바깥(DB·파일·모델·큐)에 요구하는 것을 전부 `application/ports` 의 추상 클래스로 고정한다.
Persistence 와 AI 모델 연동은 모두 `adapters/` 의 포트 구현이며, `config/` 가 유일한 조립 지점이다.

## 결과
- 유스케이스 테스트가 Fake 만으로 1초 안에 끝난다.
- 모델 교체 = 어댑터 1개 + 레지스트리 1줄 + 설정 키.
- 비용: 포트·DTO 를 하나 더 정의해야 한다. 작은 팀에는 수용 가능한 비용으로 판단.
