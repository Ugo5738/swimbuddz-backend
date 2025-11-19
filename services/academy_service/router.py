from fastapi import APIRouter

router = APIRouter(prefix="/academy", tags=["academy"])


@router.get("/")
async def list_courses():
    """
    List academy courses (Stub).
    """
    return {"message": "Academy service coming soon"}
