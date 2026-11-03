"""학교 LMS(Canvas 계열·Moodle 계열) 읽기 전용 연결. 툴 쪽 입구는 openstem.tools.lms.

- client: 설정 읽기(학교 이름·주소·토큰·캘린더 주소), 토큰을 설정한 주소에만 싣는 HTTP(크기·시간 상한)
- canvas / moodle: 각 LMS의 API를 같은 모양의 dict 목록으로 바꾸는 어댑터
- schools: 조사한 학교별 LMS 목록(주소·플랫폼·포털 → API 주소)
- feed / ical: 토큰 없이 캘린더 구독 주소(.ics)로 마감 읽기
- common: HTML→글자, 시각 표기, 파일 이름 정리
"""
