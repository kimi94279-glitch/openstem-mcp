"""학교별 LMS 목록(2026-11-03 공개 페이지 조사, 48개 대학). 데이터만 둔다.

- 학생이 아는 주소(LearningX 앞단 포털·옛 주소)는 portals에 두고, 툴은 api(실제로 API를 부를 주소)를 쓴다.
  LearningX는 호스트가 둘이다: 앞단 Laravel 포털(/api/v1 → HTTP 419 HTML)과 Canvas 본체. 중앙대는 이름이 거꾸로다.
- 지원하지 않는 곳(ILOS·Blackboard·자체 시스템)은 site에 LMS 주소만 적어 안내에 쓴다.
- token: 학생이 API 토큰을 만들 수 있을 가능성. confirmed(공개 사례가 있음) | likely(Moodle 모바일 서비스 켜짐)
  | unknown | unlikely(모바일 서비스 꺼짐 → 캘린더 주소 안내).
- notes는 LLM이 읽는 글이라 짧은 영어로 쓴다.
- 시험은 SCHOOLS를 가짜 목록으로 바꿔 끼운다(함수들이 부를 때마다 SCHOOLS를 읽는다).
"""

from __future__ import annotations

import difflib
import re
import unicodedata
from dataclasses import dataclass
from urllib.parse import urlsplit

PLATFORMS = ("learningx", "coursemos", "moodle", "blackboard", "ilos", "other")
PLATFORM_LABEL = {
    "learningx": "LearningX (Canvas)",
    "coursemos": "Coursemos (Moodle)",
    "moodle": "Moodle",
    "blackboard": "Blackboard Learn Ultra",
    "ilos": "ILOS (IMAXSOFT)",
    "other": "its own system",
}
KIND = {"learningx": "canvas", "coursemos": "moodle", "moodle": "moodle"}  # 툴이 쓰는 어댑터
CALENDAR = {"learningx": "canvas", "coursemos": "moodle", "moodle": "moodle", "blackboard": "blackboard"}


@dataclass(frozen=True)
class School:
    name: str  # 정식 이름(한국어)
    aliases: tuple[str, ...]  # 줄임말·영문·약칭
    lms: str  # 학교가 부르는 LMS 이름
    platform: str
    api: str = ""  # 툴이 API를 부를 주소(지원하는 곳만)
    portals: tuple[str, ...] = ()  # 학생이 아는 다른 주소(앞단 포털·옛 주소) → api로 바꾼다
    site: str = ""  # 지원하지 않는 곳의 LMS 주소(안내용)
    token: str = "likely"  # confirmed | likely | unknown | unlikely
    notes: str = ""

    @property
    def kind(self) -> str | None:
        return KIND.get(self.platform) if self.api else None

    @property
    def supported(self) -> bool:
        return self.kind is not None

    @property
    def calendar(self) -> str | None:
        """캘린더 구독 주소를 얻는 방법의 종류(canvas | moodle | blackboard) 또는 None."""
        return CALENDAR.get(self.platform)

    @property
    def label(self) -> str:
        return PLATFORM_LABEL.get(self.platform, self.platform)


def _lx(name: str, aliases: tuple[str, ...], lms: str, api: str, portals: tuple[str, ...] = (),
        token: str = "unknown", notes: str = "") -> School:
    return School(name, aliases, lms, "learningx", api=api, portals=portals, token=token, notes=notes)


def _cm(name: str, aliases: tuple[str, ...], lms: str, api: str, token: str = "likely", notes: str = "",
        portals: tuple[str, ...] = (), platform: str = "coursemos") -> School:
    return School(name, aliases, lms, platform, api=api, portals=portals, token=token, notes=notes)


def _no(name: str, aliases: tuple[str, ...], lms: str, platform: str, site: str, notes: str = "") -> School:
    return School(name, aliases, lms, platform, site=site, token="unlikely", notes=notes)


_MOBILE_OFF = "Mobile service is off, so a security key token is unlikely: use the calendar URL for deadlines."
_BOARD = "Notices may be in a board plugin the API cannot read."
_BB = "Blackboard gives students no API token. Its calendar may offer an iCal link (unconfirmed)."

SCHOOLS: tuple[School, ...] = (
    # ── 서울·수도권(A) ──
    _lx("서울대학교", ("서울대", "SNU", "New eTL", "eTL"), "New eTL", "https://myetl.snu.ac.kr",
        ("https://etl.snu.ac.kr",), token="confirmed",
        notes="Many courses hide the Files tab; files are then listed from modules."),
    _cm("연세대학교", ("연세대", "연대", "Yonsei", "LearnUs", "런어스"), "LearnUs", "https://ys.learnus.org",
        token="unlikely", notes=f"{_MOBILE_OFF} {_BOARD}"),
    _lx("고려대학교", ("고려대", "고대", "Korea University", "KU"), "고려대학교 LMS", "https://mylms.korea.ac.kr",
        ("https://lms.korea.ac.kr", "https://kulms.korea.ac.kr"),
        notes="Replaced Blackboard in 2025. Student tokens unconfirmed; the calendar feed is the fallback."),
    _lx("성균관대학교", ("성균관대", "성대", "SKKU", "i-Campus", "아이캠퍼스"), "i-Campus", "https://canvas.skku.edu",
        ("https://icampus.skku.edu",), token="confirmed"),
    _lx("한양대학교", ("한양대", "HYU", "Hanyang", "HY-LMS"), "HY-LMS", "https://learning.hanyang.ac.kr",
        ("https://hy-mooc.hanyang.ac.kr",), token="confirmed"),
    _no("서강대학교", ("서강대", "Sogang"), "사이버캠퍼스(e-Class)", "ilos", "https://eclass.sogang.ac.kr"),
    _lx("중앙대학교", ("중앙대", "중대", "CAU", "CAU-ON"), "e-class (CAU-ON)", "https://eclass3.cau.ac.kr",
        ("https://canvas.cau.ac.kr",),
        notes="Reversed names: canvas.cau.ac.kr is the portal, the Canvas API is eclass3.cau.ac.kr. Student tokens unconfirmed."),
    _lx("경희대학교", ("경희대", "KHU", "Kyung Hee"), "e-Campus", "https://khcanvas.khu.ac.kr",
        ("https://e-campus.khu.ac.kr",), notes="The calendar feed is known to work. Student tokens unconfirmed."),
    _no("한국외국어대학교", ("한국외대", "외대", "한국외국어대", "HUFS"), "e-Class", "ilos", "https://eclass.hufs.ac.kr"),
    _cm("서울시립대학교", ("서울시립대", "시립대", "UOS"), "uClass", "https://uclass.uos.ac.kr"),
    _cm("이화여자대학교", ("이화여대", "이대", "Ewha", "이화여자대"), "Ewha CyberCampus", "https://cyber.ewha.ac.kr"),
    _no("건국대학교", ("건국대", "건대", "Konkuk"), "eCampus", "ilos", "https://ecampus.konkuk.ac.kr"),
    _cm("동국대학교", ("동국대", "Dongguk", "DGU"), "이클래스", "https://eclass.dongguk.edu",
        token="unlikely", notes=_MOBILE_OFF),
    _cm("홍익대학교", ("홍익대", "홍대", "Hongik"), "학습관리시스템(LMS)", "https://cn2.hongik.ac.kr",
        notes="Official attendance is in ClassNet (cn.hongik.ac.kr), a separate system."),
    _cm("국민대학교", ("국민대", "Kookmin"), "가상대학 eCampus", "https://ecampus.kookmin.ac.kr"),
    _lx("숭실대학교", ("숭실대", "SSU", "Soongsil"), "숭실대학교 LMS", "https://canvas.ssu.ac.kr",
        ("https://lms.ssu.ac.kr",), token="confirmed"),
    _cm("세종대학교", ("세종대", "Sejong", "집현캠퍼스"), "집현캠퍼스", "https://ecampus.sejong.ac.kr"),
    _no("아주대학교", ("아주대", "Ajou"), "eClass", "blackboard", "https://eclass2.ajou.ac.kr", _BB),
    _cm("인하대학교", ("인하대", "Inha", "I-Class"), "I-Class", "https://learn.inha.ac.kr"),
    _no("광운대학교", ("광운대", "Kwangwoon", "KW", "KLAS"), "KLAS", "other", "https://klas.kw.ac.kr",
        "KLAS is the school's own system with no student API."),
    _no("명지대학교", ("명지대", "Myongji", "MJU"), "e-Learning System", "ilos", "https://lms.mju.ac.kr"),
    _cm("숙명여자대학교", ("숙명여대", "숙대", "Sookmyung", "SnowBoard", "숙명여자대"), "SnowBoard",
        "https://snowboard.sookmyung.ac.kr"),
    _no("가톨릭대학교", ("가톨릭대", "Catholic", "CUK"), "사이버캠퍼스", "ilos", "https://e-cyber.catholic.ac.kr"),
    _cm("상명대학교", ("상명대", "Sangmyung", "SMU"), "e-Campus", "https://ecampus.smu.ac.kr",
        token="unknown", notes="Mobile service status unknown: if there is no security key, use the calendar URL."),
    # ── 과학기술원·지방(B) ──
    _cm("한국과학기술원", ("KAIST", "카이스트", "KLMS"), "KLMS", "https://klms.kaist.ac.kr",
        platform="moodle", notes="KAIST's own Moodle."),
    _cm("포항공과대학교", ("POSTECH", "포스텍", "포항공대", "PLMS"), "PLMS", "https://plms.postech.ac.kr"),
    _no("울산과학기술원", ("UNIST", "유니스트"), "Blackboard", "blackboard", "https://blackboard.unist.ac.kr", _BB),
    _cm("광주과학기술원", ("GIST", "지스트"), "GIST LMS", "https://lms.gist.ac.kr",
        notes="Older Moodle: some functions may be missing."),
    _no("대구경북과학기술원", ("DGIST", "디지스트"), "DGIST LMS", "blackboard", "https://lms.dgist.ac.kr", _BB),
    _no("서울과학기술대학교", ("서울과기대", "서울과학기술대", "과기대", "SeoulTech"), "e-Class System", "ilos",
        "https://eclass.seoultech.ac.kr"),
    _no("한국공학대학교", ("한국공학대", "한국산업기술대", "한국산업기술대학교", "산기대", "TU Korea", "TUKorea"),
        "e-Class System", "ilos", "https://eclass.tukorea.ac.kr"),
    _cm("한국기술교육대학교", ("한국기술교육대", "코리아텍", "KOREATECH", "한기대"), "한국기술교육대LMS",
        "https://lms.koreatech.ac.kr", token="unlikely",
        notes=f"{_MOBILE_OFF} K-LXP (lxp.koreatech.ac.kr) is a different system."),
    _cm("부산대학교", ("부산대", "PNU", "Pusan", "PLATO", "플라토"), "PLATO", "https://plato.pusan.ac.kr"),
    _lx("경북대학교", ("경북대", "KNU", "Kyungpook"), "경북대학교 학습관리시스템(LearningX)", "https://canvas.knu.ac.kr",
        ("https://lms1.knu.ac.kr", "https://lms.knu.ac.kr")),
    _cm("전남대학교", ("전남대", "JNU", "Chonnam"), "JNU LMS", "https://sel.jnu.ac.kr",
        portals=("https://eclass.jnu.ac.kr",), notes="Older Moodle: some functions may be missing."),
    _cm("전북대학교", ("전북대", "JBNU", "Jeonbuk"), "JBNU LMS", "https://lms.jbnu.ac.kr"),
    _no("충남대학교", ("충남대", "CNU", "Chungnam"), "대전충남세종권역 LMS", "other", "https://dcs-learning.cnu.ac.kr",
        "A shared regional platform with no student API."),
    _cm("충북대학교", ("충북대", "CBNU", "Chungbuk"), "충북대학교LMS", "https://lms.chungbuk.ac.kr"),
    _cm("강원대학교", ("강원대", "Kangwon", "e루리", "eruri"), "강원대학교LXP(e루리)", "https://eruri.kangwon.ac.kr"),
    _lx("경상국립대학교", ("경상국립대", "경상대", "GNU", "Gyeongsang"), "LearningX(울산·경남권역)", "https://canvas.rec.ac.kr",
        ("https://www.rec.ac.kr", "https://rec.ac.kr", "https://ecampus.gnu.ac.kr"),
        notes="Shared regional Canvas (울산·경남권역)."),
    _lx("제주대학교", ("제주대", "JEJUNU", "Jeju", "JNUclass"), "JNUclass", "https://canvas.jejunu.ac.kr",
        ("https://jnuclass.jejunu.ac.kr",)),
    _no("부경대학교", ("부경대", "PKNU", "Pukyong"), "Smart-LMS", "ilos", "https://lms.pknu.ac.kr"),
    _cm("인천대학교", ("인천대", "INU", "Incheon"), "사이버캠퍼스", "https://lms.inu.ac.kr"),
    _no("울산대학교", ("울산대", "UOU", "Ulsan"), "uclass", "other", "https://uclass.ulsan.ac.kr",
        "Classum-based platform with no student API. The medical school has its own Moodle: 울산대학교 의과대학."),
    _cm("울산대학교 의과대학", ("울산대 의대", "울산의대", "울산대의대", "울산대학교의과대학"), "LCME",
        "https://lcme.ulsan.ac.kr", notes="Medical school only."),
    _no("영남대학교", ("영남대", "YU", "Yeungnam"), "e-Class System", "ilos", "https://lms.yu.ac.kr"),
    _no("계명대학교", ("계명대", "Keimyung", "KMU"), "교수학습지원시스템", "other", "https://ctl.kmu.ac.kr",
        "The school's own system with no student API."),
    _lx("단국대학교", ("단국대", "DKU", "Dankook"), "이러닝 캠퍼스(LearningX)", "https://canvas.dankook.ac.kr",
        ("https://nlms.dankook.ac.kr",)),
    _cm("한국항공대학교", ("한국항공대", "항공대", "KAU"), "KAU LMS", "https://lms.kau.ac.kr",
        notes="Older Moodle: some functions may be missing."),
)


# ── 이름 찾기 ──


def key(text: str) -> str:
    """비교용 이름: NFC, 대소문자 무시, 공백·문장부호 제거, "대학교"→"대"."""
    text = unicodedata.normalize("NFC", str(text or "")).casefold()
    text = re.sub(r"[\s\-_.·・()()\[\]'\"]+", "", text)
    return re.sub(r"대학교$", "대", text)


def _keys(school: School) -> set[str]:
    return {k for k in (key(n) for n in (school.name, *school.aliases)) if k}


def find(name: str) -> School | None:
    """이름·줄임말로 학교 하나를 찾는다. 없으면 None."""
    wanted = key(name)
    if not wanted:
        return None
    for school in SCHOOLS:
        if wanted in _keys(school):
            return school
    return None


def close_matches(name: str, limit: int = 5) -> list[School]:
    """이름이 비슷한 학교들(찾지 못했을 때 후보로 보여 준다)."""
    wanted = key(name)
    if not wanted:
        return []
    owners: dict[str, School] = {}
    for school in SCHOOLS:
        for k in _keys(school):
            owners.setdefault(k, school)
    picked: list[School] = []

    def add(school: School) -> None:
        if school not in picked:
            picked.append(school)

    for k in difflib.get_close_matches(wanted, list(owners), n=limit * 2, cutoff=0.5):
        add(owners[k])
    for k, school in owners.items():  # 일부만 넣은 경우("성균" → 성균관대)
        if len(wanted) >= 2 and (wanted in k or (len(k) >= 3 and k in wanted)):
            add(school)
    return picked[:limit]


def search(query: str | None) -> list[School]:
    """schools action용: 이름·줄임말·LMS 이름·주소에 query가 들어간 학교들."""
    if not query or not key(query):
        return list(SCHOOLS)
    exact = find(query)
    if exact:
        return [exact]
    wanted = key(query)
    hits = [
        s for s in SCHOOLS
        if any(wanted in k for k in _keys(s)) or wanted in key(s.lms) or wanted == key(s.platform)
        or any(wanted in (urlsplit(u).hostname or "") for u in (s.api, s.site, *s.portals) if u)
    ]
    return hits or close_matches(query)


def _host_port(url: str) -> tuple[str, int] | None:
    parts = urlsplit(url if "://" in url else "https://" + url)
    host = (parts.hostname or "").rstrip(".").lower()
    if not host:
        return None
    try:
        port = parts.port
    except ValueError:
        return None
    return host, port or (443 if parts.scheme.lower() == "https" else 80)


def by_host(url: str) -> tuple[School, str] | None:
    """주소의 호스트가 목록에 있으면 (학교, "api" | "portal" | "site")."""
    wanted = _host_port(url)
    if wanted is None:
        return None
    for school in SCHOOLS:
        if school.api and _host_port(school.api) == wanted:
            return school, "api"
    for school in SCHOOLS:
        if any(_host_port(p) == wanted for p in school.portals):
            return school, "portal"
        if school.site and _host_port(school.site) == wanted:
            return school, "site"
    return None
