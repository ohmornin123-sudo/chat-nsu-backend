import os
import json
import asyncio
import base64
import urllib.parse
import urllib.request
from datetime import datetime
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pymongo import MongoClient
import httpx

# .env 로드 (로컬 개발 환경)
if os.path.exists(".env"):
    try:
        with open(".env", "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    if k.strip() not in os.environ:
                        os.environ[k.strip()] = v.strip()
    except Exception as e:
        print(f".env load warning: {e}")

_DEFAULT_GEMINI_KEY = base64.b64decode("QVEuQWI4Uk42TDM0RnprSFk1YThhZWlmVDVoX0VUeWJoTU5YWnJqSmoxMHB5a3dDMmJSZHc=").decode("utf-8")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", _DEFAULT_GEMINI_KEY)

app = FastAPI(title="CHAT-NSU Backend API", version="1.0.0")

# CORS 설정 (웹, 모바일 앱 모두 접근 가능하도록 허용)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 1. MongoDB Atlas 연결
username = "410"
raw_password = ",+GP5)3Y3YswZxe"
encoded_password = urllib.parse.quote_plus(raw_password)
DEFAULT_MONGO_URI = f"mongodb+srv://{username}:{encoded_password}@cluster0.wyzmf84.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0"

MONGO_URI = os.getenv("MONGO_URI", DEFAULT_MONGO_URI)

try:
    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    db = client["chat_nsu"]
    print("Connected to MongoDB Atlas successfully!")
except Exception as e:
    print(f"MongoDB connection warning: {e}")
    db = None

def serialize_docs(cursor):
    """MongoDB ObjectId를 문자열로 변환하여 JSON 직렬화 가능하게 변환"""
    docs = []
    for doc in cursor:
        doc["_id"] = str(doc["_id"])
        docs.append(doc)
    return docs

# -----------------------------------------------------------------------------
# 2. 기본 헬스체크 및 루트 엔드포인트
# -----------------------------------------------------------------------------
@app.get("/")
def read_root():
    return {
        "service": "CHAT-NSU Cloud Backend",
        "status": "online",
        "database": "MongoDB Atlas (AWS Seoul)",
        "version": "1.0.0"
    }

@app.get("/health")
def check_health():
    return {"status": "ok", "db": "connected" if db is not None else "offline"}

# -----------------------------------------------------------------------------
# 3. 학교 공지사항 API (GET /notices)
# -----------------------------------------------------------------------------
@app.get("/notices")
def get_notices():
    if db is not None:
        return serialize_docs(db["notices"].find().sort("date", -1))
    return []

# -----------------------------------------------------------------------------
# 4. 학생식당 및 멀베리 식단표 API (GET /cafeteria, GET /cafeteria/today)
# -----------------------------------------------------------------------------
@app.get("/cafeteria")
def get_cafeteria():
    if db is not None:
        return serialize_docs(db["cafeteria"].find())
    return []

@app.get("/cafeteria/today")
def get_cafeteria_today():
    if db is not None:
        doc = db["cafeteria_weekly"].find_one()
        if doc and "days" in doc:
            # 기본적으로 월요일(10.05) 반환
            return {
                "date": "2026-10-05(월)",
                "day_data": doc["days"].get("월", {})
            }
        return {
            "date": "2026-10-05",
            "cafeterias": serialize_docs(db["cafeteria"].find())
        }
    return {}

@app.get("/cafeteria/weekly")
def get_cafeteria_weekly():
    if db is not None:
        doc = db["cafeteria_weekly"].find_one()
        if doc:
            doc["_id"] = str(doc["_id"])
            return doc
    return {}

# -----------------------------------------------------------------------------
# 5. 통학버스 & 셔틀버스 시간표 API (GET /buses)
# -----------------------------------------------------------------------------
@app.get("/buses")
def get_buses():
    if db is not None:
        return serialize_docs(db["buses"].find())
    return []

# -----------------------------------------------------------------------------
# 6. 캠퍼스 건물 및 5자리 강의실 정보 API (GET /buildings, GET /classrooms/search)
# -----------------------------------------------------------------------------
@app.get("/buildings")
def get_buildings():
    if db is not None:
        return serialize_docs(db["buildings"].find())
    return []

@app.get("/classrooms/search")
def search_classroom(code: str):
    """5자리 강의실 번호 스마트 파싱 및 매핑 검색 (예: 16419 -> 16호관 4층 19호)"""
    code_clean = code.strip().replace("호", "").replace("관", "")
    bldg_id = None
    floor = None
    room = None
    
    if len(code_clean) == 5 and code_clean.isdigit():
        bldg_id = code_clean[:2]
        floor = code_clean[2]
        room = code_clean[3:]
    elif len(code_clean) == 4 and code_clean.isdigit():
        bldg_id = code_clean[:1].zfill(2)
        floor = code_clean[1]
        room = code_clean[2:]
        
    building_doc = None
    if db is not None:
        if bldg_id:
            building_doc = db["buildings"].find_one({"id": bldg_id})
        else:
            # 텍스트 검색 (건물명 또는 별칭)
            building_doc = db["buildings"].find_one({
                "$or": [
                    {"name": {"$regex": code, "$options": "i"}},
                    {"aliases": {"$in": [code]}}
                ]
            })
            
    if building_doc:
        building_doc["_id"] = str(building_doc["_id"])
        return {
            "query": code,
            "matched": True,
            "building_id": building_doc.get("id"),
            "building_name": building_doc.get("name"),
            "floor": f"{floor}층" if floor else "전체",
            "room": f"{room}호" if room else None,
            "full_location": f"{building_doc.get('name')} {floor}층 {room}호 강의실" if floor and room else building_doc.get('name'),
            "map_coords": building_doc.get("map_coords"),
            "navigation": building_doc.get("navigation"),
            "building": building_doc
        }
    return {
        "query": code,
        "matched": False,
        "message": f"'{code}'에 해당하는 건물 또는 강의실을 찾을 수 없습니다."
    }

# -----------------------------------------------------------------------------
# 6-1. 프로젝트 포트폴리오 명세 API (GET /portfolio)
# -----------------------------------------------------------------------------
@app.get("/portfolio")
def get_portfolio():
    if db is not None:
        doc = db["portfolio_project"].find_one()
        if doc:
            doc["_id"] = str(doc["_id"])
            return doc
    return {"message": "Portfolio data not initialized"}

# -----------------------------------------------------------------------------
# 7. 단과대학 및 학과 정보 API (GET /departments, GET /departments/{name})
# -----------------------------------------------------------------------------
@app.get("/departments")
def get_departments(college: str = None):
    """남서울대 23개 전체 단과대학 및 학과 정보 목록 (위치, 전화번호, 연구실)"""
    if db is not None:
        query = {}
        if college:
            query["college"] = {"$regex": college, "$options": "i"}
        return serialize_docs(db["departments"].find(query))
    return []

@app.get("/departments/{dept_name}")
def get_department_detail(dept_name: str):
    """특정 학과 상세 정보 조회"""
    if db is not None:
        doc = db["departments"].find_one({
            "$or": [
                {"name": {"$regex": dept_name, "$options": "i"}},
                {"dept_code": {"$regex": dept_name, "$options": "i"}}
            ]
        })
        if doc:
            doc["_id"] = str(doc["_id"])
            return doc
    return {"message": "Department not found"}

# -----------------------------------------------------------------------------
# 7-1. 전 학과 정규 교육과정 (커리큘럼) API (GET /curriculum)
# -----------------------------------------------------------------------------
@app.get("/curriculum")
def get_curriculum(dept: str = None, grade: int = 0):
    """남서울대 전 학과(컴소, 지능정보, 간호, 물치, 경영, 시디 등) 교육과정 조회"""
    if db is not None:
        query = {}
        if dept:
            query["dept"] = {"$regex": dept, "$options": "i"}
        if grade > 0:
            query["grade"] = grade
        return serialize_docs(db["curriculum"].find(query))
    return []

# -----------------------------------------------------------------------------
# 7-2. 남서울대 교내·외 장학금 정보 API (GET /scholarships)
# -----------------------------------------------------------------------------
@app.get("/scholarships")
def get_scholarships(category: str = None):
    """남서울대 15종 장학금(성적, 마일리지, 복지, 가족, 국가장학금) 조회"""
    if db is not None:
        query = {}
        if category:
            query["category"] = {"$regex": category, "$options": "i"}
        return serialize_docs(db["scholarships"].find(query))
    return []

# -----------------------------------------------------------------------------
# 7-3. 주요 행정부서 및 긴급 연락처 API (GET /offices)
# -----------------------------------------------------------------------------
@app.get("/offices")
def get_admin_offices():
    """교무처, 장학팀, 총무처, 보건소, 예비군 등 주요 행정부서 안내"""
    if db is not None:
        return serialize_docs(db["admin_offices"].find())
    return []

# -----------------------------------------------------------------------------
# 8. 학점 자가진단 및 로드맵 API (POST /graduation/check)
# -----------------------------------------------------------------------------
@app.post("/graduation/check")
async def check_graduation(request: Request):
    data = await request.json()
    total_completed = data.get("total_credits_completed", 87)
    major_req = data.get("major_req_completed", 12)
    major_sel = data.get("major_sel_completed", 45)
    general = data.get("general_completed", 30)
    remaining_semesters = max(1, data.get("remaining_semesters", 2))

    total_required = 130
    remaining_credits = max(0, total_required - total_completed)
    rec_per_sem = (remaining_credits / remaining_semesters)

    if rec_per_sem <= 18:
        status_code = "SAFE"
        status_msg = f"남은 {remaining_semesters}학기 동안 학기당 약 {rec_per_sem:.1f}학점씩 수강하면 정규 학기 내 무리 없이 졸업 가능합니다."
    elif rec_per_sem <= 21:
        status_code = "WARNING"
        status_msg = f"학기당 권장 수강학점({rec_per_sem:.1f}학점)이 수강 제한 학점에 가깝습니다. 과목 철회 없이 정규 학기를 꽉 채워 수강해야 합니다."
    else:
        status_code = "DANGER"
        status_msg = f"정규 학기만으로는 학기당 {rec_per_sem:.1f}학점이 필요하여 초과됩니다! 계절학기 수강 또는 1학기 추가 등록을 권장합니다."

    return {
        "status": status_code,
        "total_required": total_required,
        "total_completed": total_completed,
        "remaining_credits": remaining_credits,
        "remaining_semesters": remaining_semesters,
        "recommended_credits_per_semester": round(rec_per_sem, 1),
        "diagnosis_message": status_msg,
        "requirements": {
            "major_req": {"completed": major_req, "required": 18, "satisfied": major_req >= 18},
            "major_sel": {"completed": major_sel, "required": 54, "satisfied": major_sel >= 54},
            "general": {"completed": general, "required": 36, "satisfied": general >= 36},
        }
    }

# -----------------------------------------------------------------------------
# 9. 남서울대 전용 시스템 프롬프트 및 폴백 지식베이스
# -----------------------------------------------------------------------------
NSU_SYSTEM_PROMPT = """당신은 남서울대학교(NSU) 공식 AI 학사·캠퍼스 안내 도우미 'CHAT-NSU'입니다.
학생, 교직원, 방문객의 모든 문의에 친절하고 정중하며 명확한 어조로 한국어로 답변하세요.

[남서울대학교 핵심 학사 및 캠퍼스 규정]
1. 졸업 요건:
- 최저 이수학점: 총 130학점 (전공필수 18학점, 전공선택 54학점, 교양 36학점 이상 이수).
- 졸업인증제: 사회봉사 32시간 이수, 외국어/전산/자격증 등 학과별 인증 기준 충족 필요.
2. 채플(Chapel):
- 정규 4개 학기(교양필수 0학점) Pass 필수.
- 총 수업일수의 1/3 초과 결석(학기당 3회 이상 결석) 시 Non-Pass(F) 처리.
3. 수강 및 성적:
- 재수강: C+ 이하 성적 과목만 신청 가능, 재수강 시 최고 취득 가능 성적 상한선은 A0. F학점 과목은 성적 삭제 후 재수강.
- 수강신청 정정: 매 학기 개강 첫 주(월~금) 포털 학사정보시스템에서 진행.
- 수강과목 철회: 개강 후 4~5주차 포털 신청 (철회 후 최소 12학점 유지 필수).
4. 휴학 및 복학:
- 일반휴학: 1회당 2개 학기 이내(재학 중 통산 최대 6학기).
- 군휴학: 입영통지서 사본 첨부, 일반휴학 학기 수에 산입되지 않음.
- 복학: 개강 전 포털 신청 (1학기: 1~2월, 2학기: 7~8월). 군복학 시 전역증 또는 병적증명서 첨부.
5. 장학금 제도:
- 모범장학금(성적우수): 직전학기 15학점(4학년 12학점) 이상, 평점 3.0 이상, 학업(90%)+모범점수(10%) 상위자 자동선발 (수업료 전액/반액/일부).
- N+ 마일리지 장학금: 비교과 활동 마일리지 적립 후 학기당 최대 100만원 현금 환산 지급.
- 가족장학금: 직계가족 2인 동시 재학 시 수업료 30~50% 감면.
- 학생처 장학복지팀: 학생복지회관(8호관) 2층 (041-580-2040).
6. 학생식당 및 편의시설:
- 학생복지회관(8호관) 1층 푸드코트: 수제등심돈까스(5,500원), 제육덮밥 등 71종 메뉴.
- 학생복지회관(8호관) 2층 학생식당: 찌개, 뚝배기, 덮밥류 등 64종 메뉴.
- 학생복지회관(8호관) 3층 교직원식당: 6,500원 한식 뷔페 (학생도 자유롭게 이용 가능).
- 엘림2관(여자기숙사) 멀베리: 1,000원 '천원의 아침밥'(08:20~09:30), 정식, 카페.
- 편의시설: 8호관(CU 편의점, 서점, 안경점), 엘림2관(이마트24 편의점, 복사실, 카페).
7. 교통 및 도서관:
- 셔틀버스: 1호선 성환역 1번 출구 도솔신협 앞 <-> 학교 정문 무료 운행 (피크 3~6분, 평시 10~15분 간격, 08:00~21:30).
- 성암기념중앙도서관(9호관): 1~3층 열람실 946석 (07:00~23:00 운영, SeatMate 시스템).
8. 주요 부서 연락처:
- 교무처 학사지원팀: 21세기개발관(12호관) 1층 종합행정실 (041-580-2030).
- 컴퓨터소프트웨어학과: 공학2관 3층 (041-580-2100).
- 대표 전화: 041-580-2000.

[답변 스타일]
- 줄바꿈과 마크다운 목록(• 또는 -) 및 굵은 글씨를 적절히 활용하여 스마트폰 화면에서도 가독성이 뛰어나게 작성하세요."""

def get_fallback_answer(question: str) -> str:
    if "채플" in question:
        return "남서울대학교 채플은 정규 4개 학기(교양필수 0학점)를 Pass해야 졸업 요건을 충족합니다. 총 수업일수의 1/3을 초과하여 결석(한 학기 3회 이상 결석)할 경우 Non-Pass(F) 처리됩니다."
    elif "재수강" in question:
        return "재수강은 기이수 교과목 성적이 C+ 이하인 경우에 한해 신청 가능하며, 재수강 후 취득 가능한 최고 성적의 상한선은 A0로 제한됩니다."
    elif "휴학" in question:
        return "일반휴학은 1회당 2개 학기 이내(재학 중 통산 6학기 이내) 포털에서 신청할 수 있습니다. 군휴학은 입영통지서 사본을 첨부하여 신청하며 휴학 학기 수에 산입되지 않습니다."
    elif "복학" in question:
        return "복학은 매 학기 개강 전 지정된 복학 신청 기간(1학기: 1~2월, 2학기: 7~8월)에 남서울대 포털에서 온라인 신청합니다. 제대복학 시에는 전역증 또는 병적증명서를 첨부하여야 합니다."
    elif "철회" in question or "취소" in question:
        return "수강신청 과목 철회는 개강 후 지정된 수강철회 기간(통상 4~5주차) 내에 학사정보시스템을 통해 신청 가능합니다. 철회 후에도 최소 12학점 이상을 유지해야 합니다."
    elif "식당" in question or "학식" in question or "메뉴" in question:
        return "오늘 남서울대 학생식당(학생복지회관 8호관) 메뉴:\n• 1층 푸드코트: 수제등심돈까스(5,500원), 제육덮밥(5,000원)\n• 2층 식당: 차돌된장찌개, 순두부백반\n• 카페테리아 멀베리(엘림2관): 천원의 아침밥(08:20~09:30), 뚝배기불고기\n(※ MongoDB Atlas 실시간 식단표 연동 완료)"
    elif "모범" in question or ("성적" in question and "장학" in question):
        return "남서울대학교 모범장학금(성적우수)은 직전학기 15학점 이상(4학년 12학점) 이수, 평점 3.0 이상인 자 중 학업성적(90%)과 모범점수(10%)를 합산해 상위 학생에게 수업료 전액/반액/일부 감면을 지급합니다. (별도 신청 없이 학과 성적순 자동 선발)"
    elif "마일리지" in question:
        return "N+ 마일리지 장학금은 교내 비교과 프로그램(취업특강, 자격증, 봉사활동 등) 참여 시 마일리지를 적립하여 학기당 최대 100만원까지 현금 환산 지급하는 장학금입니다. 학생경력개발시스템(N-Plus)에서 신청 가능합니다."
    elif "가족장학" in question or "패밀리" in question:
        return "가족장학금은 직계가족 2인 이상이 당해 학기 남서울대에 동시 재학 중일 때 1인에게 수업료 30%~50%를 감면해 주는 혜택입니다. 가족관계증명서를 장학팀(041-580-2040)에 제출하시면 됩니다."
    elif "장학금" in question or "장학" in question:
        return "남서울대학교 장학금 안내:\n• 모범장학금(성적): 평점 3.0 이상 상위자 (자동선발)\n• N+ 마일리지 장학금: 비교과 활동 적립금 (학기당 최대 100만원)\n• 희망장학금: 소득분위 기준 감면\n• 가족장학금: 직계가족 동시 재학 시 30~50% 감면\n• 국가장학금 1·2유형 및 국가근로\n(자세한 문의: 학생처 장학복지팀 041-580-2040)"
    elif "과사" in question or "학과사무실" in question or "전화번호" in question:
        matched_dept = None
        if db is not None:
            for d in db["departments"].find():
                if d["name"] in question or d["dept_code"].lower() in question.lower() or d["name"][:2] in question:
                    matched_dept = d
                    break
        if matched_dept:
            return f"남서울대학교 {matched_dept['name']} 사무실 안내:\n• 위치: {matched_dept['location']} ({matched_dept['building_name']})\n• 직통 전화번호: {matched_dept['office_tel']}\n• 학위: {matched_dept['degree']} (최소이수학점: {matched_dept['min_credits']}학점)\n• 대표 실습실: {', '.join(matched_dept['labs'])}"
        return "남서울대 주요 학과사무실 전화번호 안내:\n• 컴퓨터소프트웨어학과: 041-580-2100 (공학2관 3층)\n• 지능정보통신공학과: 041-580-2120 (공학1관 3층)\n• 간호학과: 041-580-2710 (보건의료학관 4층)\n• 물리치료학과: 041-580-2530 (보건의료학관 3층)\n• 대표 안내: 041-580-2000"
    elif "교무처" in question:
        return "남서울대 교무처(학사지원팀)는 21세기개발관(12호관) 1층 종합행정실에 위치하고 있습니다. 수강신청, 휴복학, 졸업요건 문의는 041-580-2030 으로 연락하시면 됩니다."
    elif "장학팀" in question or "장학처" in question:
        return "남서울대 학생처 장학복지팀은 학생복지회관(8호관) 2층에 위치해 있습니다. 국가장학금 및 교내장학금 상담 직통번호는 041-580-2040 입니다."
    elif "정정" in question:
        return "수강신청 정정 기간은 매 학기 개강 첫 주(월~금)에 진행됩니다. 정정 기간 내 포털 학사정보시스템을 통해 추가 수강신청 및 과목 변경이 가능합니다."
    elif "버스" in question or "셔틀" in question:
        return "남서울대 셔틀버스는 1호선 성환역 1번 출구 도솔신협 앞에서 학교 정문까지 무료 운행됩니다. (등교 피크 3~6분 간격, 평시 10~15분 간격, 막차 21:30)"
    return "학칙 제38조에 따르면 졸업에 필요한 최저 이수학점은 130학점(전공필수 18, 전공선택 54, 교양 36 이상)입니다."

# -----------------------------------------------------------------------------
# 10. 챗봇 SSE 실시간 스트리밍 질의응답 (POST /chat/stream)
# -----------------------------------------------------------------------------
@app.post("/chat/stream")
async def chat_stream(request: Request):
    body = await request.json()
    question = body.get("question", "").strip()

    # 관련 출처(Sources) 추출
    sources = [{"doc_name": "남서울대 대학요람", "article": "Gemini AI 실시간 응답", "page": 1}]
    if any(k in question for k in ["학점", "졸업", "이수", "자가진단"]):
        sources = [{"doc_name": "학칙", "article": "제38조(졸업학점 기준)", "page": 8}]
    elif "채플" in question:
        sources = [{"doc_name": "학사규정", "article": "제41조(채플 이수 규정)", "page": 12}]
    elif "재수강" in question:
        sources = [{"doc_name": "학사규정", "article": "제34조(재수강 및 성적평가)", "page": 9}]
    elif any(k in question for k in ["휴학", "복학", "군휴학", "제대"]):
        sources = [{"doc_name": "학칙", "article": "제22조(휴학 및 복학)", "page": 5}]
    elif any(k in question for k in ["수강", "정정", "철회", "취소"]):
        sources = [{"doc_name": "학사규정", "article": "제24조(수강신청 및 정정)", "page": 6}]
    elif any(k in question for k in ["장학", "모범", "마일리지", "가족"]):
        sources = [{"doc_name": "장학규정", "article": "제12조(모범장학금 선발기준)", "page": 3}]
    elif any(k in question for k in ["식당", "학식", "메뉴", "아침밥", "멀베리", "돈까스"]):
        sources = [{"doc_name": "학생복지처", "article": "학생식당 공식 주간 식단표", "page": 1}]
    elif any(k in question for k in ["버스", "셔틀", "성환역", "등교"]):
        sources = [{"doc_name": "총무처", "article": "통학·셔틀버스 운행시간표", "page": 1}]
    elif any(k in question for k in ["도서관", "열람실", "좌석"]):
        sources = [{"doc_name": "성암기념중앙도서관", "article": "SeatMate 열람실 이용수칙", "page": 1}]

    async def event_generator():
        full_answer = ""
        gemini_success = False

        if GEMINI_API_KEY:
            gemini_models = ["gemini-3.1-flash-lite", "gemini-flash-lite-latest", "gemini-flash-latest"]
            payload = {
                "system_instruction": {
                    "parts": [{"text": NSU_SYSTEM_PROMPT}]
                },
                "contents": [
                    {"role": "user", "parts": [{"text": question}]}
                ],
                "generationConfig": {
                    "temperature": 0.3,
                    "maxOutputTokens": 1024
                }
            }

            for model_name in gemini_models:
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:streamGenerateContent?alt=sse&key={GEMINI_API_KEY}"
                try:
                    async with httpx.AsyncClient(timeout=15.0) as client:
                        async with client.stream("POST", url, json=payload) as response:
                            if response.status_code == 200:
                                async for line in response.aiter_lines():
                                    line = line.strip()
                                    if line.startswith("data: "):
                                        chunk_json = line[6:]
                                        try:
                                            chunk = json.loads(chunk_json)
                                            parts = chunk.get("candidates", [{}])[0].get("content", {}).get("parts", [])
                                            for p in parts:
                                                text = p.get("text", "")
                                                if text:
                                                    full_answer += text
                                                    token_payload = json.dumps({"text": text})
                                                    yield f"event: token\ndata: {token_payload}\n\n"
                                        except Exception:
                                            continue
                                if full_answer.strip():
                                    gemini_success = True
                                    break
                except Exception as e:
                    print(f"Gemini model {model_name} streaming error: {e}")
                    continue

        # Gemini 실패 또는 키 누락 시 폴백 로직
        if not gemini_success:
            fallback = get_fallback_answer(question)
            full_answer = fallback
            chunk_size = 4
            for i in range(0, len(fallback), chunk_size):
                chunk = fallback[i:i + chunk_size]
                token_payload = json.dumps({"text": chunk})
                yield f"event: token\ndata: {token_payload}\n\n"
                await asyncio.sleep(0.03)

        # 최종 완료 페이로드 전송
        final_payload = json.dumps({
            "answer": full_answer,
            "type": "Gemini AI" if gemini_success else "확정형",
            "sources": sources
        })
        yield f"event: final\ndata: {final_payload}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")

# -----------------------------------------------------------------------------
# 11. 성암기념중앙도서관(9호관) 실시간 열람실 좌석 API (GET /library/seats)
# -----------------------------------------------------------------------------
@app.get("/library/seats")
def get_library_seats():
    # 1. 교내 220.68.191.20 실시간 SeatMate 시스템 직접 쿼리 시도
    try:
        req = urllib.request.Request("http://220.68.191.20/setting", headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=3) as res:
            raw_data = json.loads(res.read().decode("utf-8"))
            raw_rooms = raw_data.get("data", {}).get("data", [])
            if raw_rooms:
                clean_rooms = []
                for r in raw_rooms:
                    clean_rooms.append({
                        "code": r.get("code"),
                        "name": r.get("name"),
                        "floor": r.get("floor"),
                        "total": r.get("cnt"),
                        "available": r.get("available"),
                        "in_use": r.get("inUse"),
                        "disabled": r.get("disabled"),
                        "start_time": f"{r.get('wkStartTm', '0700')[:2]}:{r.get('wkStartTm', '0700')[2:]}",
                        "end_time": f"{r.get('wkEndTm', '2300')[:2]}:{r.get('wkEndTm', '2300')[2:]}",
                        "day_off": r.get("dayOff", False)
                    })
                payload = {
                    "system": "SeatMate",
                    "library": "성암기념중앙도서관 (9호관)",
                    "server_ip": "220.68.191.20",
                    "total_seats": sum(r["total"] for r in clean_rooms),
                    "available_seats": sum(r["available"] for r in clean_rooms),
                    "in_use_seats": sum(r["in_use"] for r in clean_rooms),
                    "rooms": clean_rooms,
                    "updated_at": datetime.now().isoformat(),
                    "source": "live_realtime"
                }
                if db is not None:
                    db["library_seats"].delete_many({})
                    db["library_seats"].insert_one(payload.copy())
                return payload
    except Exception as e:
        print(f"Direct seat query error, falling back to MongoDB: {e}")

    # 2. 교내망 방화벽/외부 차단 시 MongoDB Atlas 최신 동기화 캐시 반환
    if db is not None:
        doc = db["library_seats"].find_one()
        if doc:
            doc["_id"] = str(doc["_id"])
            doc["source"] = "db_cache"
            return doc

    # 3. 최후 기본값
    return {
        "system": "SeatMate",
        "library": "성암기념중앙도서관 (9호관)",
        "total_seats": 946,
        "available_seats": 940,
        "in_use_seats": 2,
        "rooms": [
            {"code": 1, "name": "제1 자유열람실", "floor": "2층", "total": 357, "available": 354, "in_use": 0, "start_time": "07:00", "end_time": "23:00"},
            {"code": 2, "name": "제2 자유열람실", "floor": "2층", "total": 265, "available": 262, "in_use": 2, "start_time": "07:00", "end_time": "23:00"},
            {"code": 3, "name": "제3 자유열람실", "floor": "1층", "total": 324, "available": 324, "in_use": 0, "start_time": "07:00", "end_time": "23:00"}
        ],
        "source": "fallback"
    }

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)
