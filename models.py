from sqlalchemy import Column, String, Boolean, DateTime, func
from sqlalchemy.dialects.postgresql import JSONB
from database import Base

class User(Base):
    __tablename__ = "users"

    id = Column(String(255), primary_key=True)
    email = Column(String(500), nullable=False)
    name = Column(String(500), nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    last_login = Column(DateTime, server_default=func.now())
    is_active = Column(Boolean, server_default=func.true())
    # 'metadata' is a reserved attribute in SQLAlchemy models (Base.metadata),
    # so we map the database column "metadata" to the Python attribute 'user_metadata'.
    user_metadata = Column("metadata", JSONB, server_default='{}')

    def __repr__(self):
        return f"<User(id={self.id}, email={self.email})>"
