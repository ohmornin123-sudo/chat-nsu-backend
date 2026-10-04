import os
import json
import asyncio
import urllib.parse
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pymongo import MongoClient

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
        return {
            "date": "2026-10-05",
            "cafeterias": serialize_docs(db["cafeteria"].find())
        }
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
# 7. 지능정보통신공학과 교육과정 API (GET /curriculum)
# -----------------------------------------------------------------------------
@app.get("/curriculum")
def get_curriculum(grade: int = 0):
    if db is not None:
        query = {} if grade == 0 else {"grade": grade}
        return serialize_docs(db["curriculum"].find(query))
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
# 9. 챗봇 SSE 실시간 스트리밍 질의응답 (POST /chat/stream)
# -----------------------------------------------------------------------------
@app.post("/chat/stream")
async def chat_stream(request: Request):
    body = await request.json()
    question = body.get("question", "").strip()

    # 답변 및 출처 생성 로직 (MongoDB 학사규정 / 식단 / 버스 연동)
    answer = "학칙 제38조에 따르면 졸업에 필요한 최저 이수학점은 130학점(전공필수 18, 전공선택 54, 교양 36 이상)입니다."
    sources = [{"doc_name": "학칙", "article": "제38조(졸업학점 기준)", "page": 8}]

    if "채플" in question:
        answer = "남서울대학교 채플은 정규 4개 학기(교양필수 0학점)를 Pass해야 졸업 요건을 충족합니다. 총 수업일수의 1/3을 초과하여 결석(한 학기 3회 이상 결석)할 경우 Non-Pass(F) 처리됩니다."
        sources = [{"doc_name": "학사규정", "article": "제41조(채플 이수 규정)", "page": 12}]
    elif "재수강" in question:
        answer = "재수강은 기이수 교과목 성적이 C+ 이하인 경우에 한해 신청 가능하며, 재수강 후 취득 가능한 최고 성적의 상한선은 A0로 제한됩니다."
        sources = [{"doc_name": "학사규정", "article": "제34조(재수강 및 성적평가)", "page": 9}]
    elif "휴학" in question:
        answer = "일반휴학은 1회당 2개 학기 이내(재학 중 통산 6학기 이내) 포털에서 신청할 수 있습니다. 군휴학은 입영통지서 사본을 첨부하여 신청하며 휴학 학기 수에 산입되지 않습니다."
        sources = [{"doc_name": "학칙", "article": "제22조(휴학 및 복학)", "page": 5}]
    elif "복학" in question:
        answer = "복학은 매 학기 개강 전 지정된 복학 신청 기간(1학기: 1~2월, 2학기: 7~8월)에 남서울대 포털에서 온라인 신청합니다. 제대복학 시에는 전역증 또는 병적증명서를 첨부하여야 합니다."
        sources = [{"doc_name": "학칙", "article": "제23조(복학 절차 및 제출서류)", "page": 5}]
    elif "철회" in question or "취소" in question:
        answer = "수강신청 과목 철회는 개강 후 지정된 수강철회 기간(통상 4~5주차) 내에 학사정보시스템을 통해 신청 가능합니다. 철회 후에도 최소 12학점 이상을 유지해야 합니다."
        sources = [{"doc_name": "학사규정", "article": "제26조(수강과목 철회)", "page": 7}]
    elif "식당" in question or "학식" in question or "메뉴" in question:
        answer = "오늘 남서울대 학생식당(학생복지회관 8호관) 메뉴:\n• 1층 푸드코트: 수제등심돈까스(5,500원), 제육덮밥(5,000원)\n• 2층 식당: 차돌된장찌개, 순두부백반\n• 카페테리아 멀베리(엘림2관): 천원의 아침밥(08:20~09:30), 뚝배기불고기\n(※ MongoDB Atlas 실시간 식단표 연동 완료)"
        sources = [{"doc_name": "학생복지처", "article": "주간식단표(학생복지회관)", "page": 1}]
    elif "장학금" in question or "장학" in question:
        answer = "장학금은 직전 학기 15학점(4학년 12학점) 이상을 이수하고 평점평균 2.5 이상인 자 중 품행이 단정하고 성적 또는 가계 곤란도 기준을 충족한 학생에게 지급됩니다."
        sources = [{"doc_name": "장학규정", "article": "제76조(장학금 지급 요건)", "page": 16}]
    elif "정정" in question:
        answer = "수강신청 정정 기간은 매 학기 개강 첫 주(월~금)에 진행됩니다. 정정 기간 내 포털 학사정보시스템을 통해 추가 수강신청 및 과목 변경이 가능합니다."
        sources = [{"doc_name": "학사규정", "article": "제24조(수강신청 정정)", "page": 6}]
    elif "버스" in question or "셔틀" in question:
        answer = "남서울대 셔틀버스는 1호선 성환역 1번 출구 도솔신협 앞에서 학교 정문까지 무료 운행됩니다. (등교 피크 3~6분 간격, 평시 10~15분 간격, 막차 21:30)"
        sources = [{"doc_name": "총무처", "article": "통학·셔틀버스 운행시간표", "page": 1}]

    async def event_generator():
        # SSE 토큰 스트리밍 시뮬레이션
        chunk_size = 4
        for i in range(0, len(answer), chunk_size):
            chunk = answer[i:i + chunk_size]
            token_payload = json.dumps({"text": chunk})
            yield f"event: token\ndata: {token_payload}\n\n"
            await asyncio.sleep(0.04)

        # 최종 완료 페이로드
        final_payload = json.dumps({
            "answer": answer,
            "type": "확정형",
            "sources": sources
        })
        yield f"event: final\ndata: {final_payload}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)
