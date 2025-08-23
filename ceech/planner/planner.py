import os
from datetime import datetime
from typing import Optional, AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import Integer, String, DateTime, Boolean, select, update
from pydantic import BaseModel, ConfigDict
import uvicorn


class Base(DeclarativeBase):
    pass


class Goal(Base):
    __tablename__ = "goals"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    description: Mapped[str] = mapped_column(String, nullable=False)
    deadline: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class GoalBase(BaseModel):
    description: str
    deadline: datetime


class GoalCreate(GoalBase):
    pass


class GoalResponse(GoalBase):
    model_config = ConfigDict(from_attributes=True)
    
    id: int
    is_active: bool
    created_at: datetime


class DatabaseManager:
    def __init__(self):
        self.engine = None
        self.session_maker = None
    
    async def initialize(self):
        database_url = (
            f"postgresql+asyncpg://{os.getenv('POSTGRES_USER')}:"
            f"{os.getenv('POSTGRES_PASSWORD')}@{os.getenv('POSTGRES_HOST')}:"
            f"{os.getenv('POSTGRES_PORT')}/{os.getenv('POSTGRES_DB')}"
        )
        
        self.engine = create_async_engine(
            database_url,
            echo=False,
            pool_size=20,
            max_overflow=30,
            pool_pre_ping=True,
            pool_recycle=3600
        )
        
        self.session_maker = async_sessionmaker(
            self.engine,
            class_=AsyncSession,
            expire_on_commit=False
        )
        
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    
    async def get_session(self) -> AsyncGenerator[AsyncSession, None]:
        async with self.session_maker() as session:
            try:
                yield session
            except Exception:
                await session.rollback()
                raise
            finally:
                await session.close()
    
    async def shutdown(self):
        if self.engine:
            await self.engine.dispose()


db_manager = DatabaseManager()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db_manager.initialize()
    yield
    await db_manager.shutdown()


app = FastAPI(
    title="Planner Agent API",
    description="Production-grade goal management system",
    version="1.0.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async for session in db_manager.get_session():
        yield session


@app.post("/api/goals/", response_model=GoalResponse, status_code=status.HTTP_201_CREATED)
async def create_goal(
    goal_data: GoalCreate,
    db: AsyncSession = Depends(get_db)
) -> GoalResponse:
    await db.execute(update(Goal).values(is_active=False))
    
    goal = Goal(
        description=goal_data.description,
        deadline=goal_data.deadline,
        is_active=True
    )
    
    db.add(goal)
    await db.commit()
    await db.refresh(goal)
    
    return GoalResponse.model_validate(goal)


@app.get("/api/goals/active", response_model=Optional[GoalResponse])
async def get_active_goal(db: AsyncSession = Depends(get_db)) -> Optional[GoalResponse]:
    result = await db.execute(
        select(Goal).where(Goal.is_active == True).limit(1)
    )
    goal = result.scalar_one_or_none()
    
    if not goal:
        return None
    
    return GoalResponse.model_validate(goal)


@app.get("/health")
async def health_check():
    return {"status": "healthy", "timestamp": datetime.utcnow()}


if __name__ == "__main__":
    uvicorn.run(
        "planner:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
        workers=1,
        log_level="info"
    )