import uuid
from datetime import datetime
from typing import Optional
from sqlalchemy import String, Integer, Float, Boolean, DateTime, Text, JSON, ForeignKey, Enum
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
import enum


class Base(DeclarativeBase):
    pass


class CallMode(str, enum.Enum):
    outbound = "outbound"
    inbound = "inbound"


class CallStatus(str, enum.Enum):
    pending = "pending"
    dialing = "dialing"
    active = "active"
    completed = "completed"
    failed = "failed"
    no_answer = "no_answer"
    busy = "busy"
    voicemail = "voicemail"


class CampaignStatus(str, enum.Enum):
    pending = "pending"
    running = "running"
    paused = "paused"
    completed = "completed"


class Language(str, enum.Enum):
    en = "en"
    fr = "fr"


class Campaign(Base):
    """Outbound calling campaign created from CSV/Excel upload."""
    __tablename__ = "campaigns"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(255))
    task_type: Mapped[str] = mapped_column(String(100))  # delivery, survey, reminder, etc.
    status: Mapped[CampaignStatus] = mapped_column(Enum(CampaignStatus), default=CampaignStatus.pending)
    total_contacts: Mapped[int] = mapped_column(Integer, default=0)
    completed_calls: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    metadata_: Mapped[Optional[dict]] = mapped_column("metadata", JSON, nullable=True)

    contacts: Mapped[list["Contact"]] = relationship("Contact", back_populates="campaign")


class Contact(Base):
    """Individual contact from CSV/Excel upload."""
    __tablename__ = "contacts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    campaign_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("campaigns.id"), nullable=True)
    first_name: Mapped[str] = mapped_column(String(100))
    last_name: Mapped[str] = mapped_column(String(100))
    phone: Mapped[str] = mapped_column(String(20))  # E.164 format
    reason_for_call: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    language: Mapped[Language] = mapped_column(Enum(Language), default=Language.en)
    custom_data: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)  # extra CSV columns
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    campaign: Mapped[Optional["Campaign"]] = relationship("Campaign", back_populates="contacts")
    calls: Mapped[list["Call"]] = relationship("Call", back_populates="contact")


class Call(Base):
    """Individual call record."""
    __tablename__ = "calls"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    twilio_call_sid: Mapped[Optional[str]] = mapped_column(String(50), nullable=True, unique=True)
    contact_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("contacts.id"), nullable=True)
    campaign_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("campaigns.id"), nullable=True)
    mode: Mapped[CallMode] = mapped_column(Enum(CallMode))
    task_type: Mapped[str] = mapped_column(String(100))
    status: Mapped[CallStatus] = mapped_column(Enum(CallStatus), default=CallStatus.pending)
    detected_language: Mapped[Language] = mapped_column(Enum(Language), default=Language.en)
    duration_seconds: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    cost_usd: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    ended_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    contact: Mapped[Optional["Contact"]] = relationship("Contact", back_populates="calls")
    report: Mapped[Optional["CallReport"]] = relationship("CallReport", back_populates="call", uselist=False)


class CallReport(Base):
    """Structured report generated after each call."""
    __tablename__ = "call_reports"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    call_id: Mapped[str] = mapped_column(String(36), ForeignKey("calls.id"), unique=True)
    outcome: Mapped[str] = mapped_column(String(100))  # confirmed, declined, booked, transferred, etc.
    sentiment: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)  # positive, neutral, negative
    task_completed: Mapped[bool] = mapped_column(Boolean, default=False)
    extracted_data: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)  # structured task data
    transcript: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)  # [{role, content, timestamp}]
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # human-readable summary
    follow_up_required: Mapped[bool] = mapped_column(Boolean, default=False)
    follow_up_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    call: Mapped["Call"] = relationship("Call", back_populates="report")


class InboundConfig(Base):
    """Configuration for inbound call handling (restaurant, hotel, support, etc.)."""
    __tablename__ = "inbound_configs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(255))
    task_type: Mapped[str] = mapped_column(String(100))  # restaurant, hotel, support, custom
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    language: Mapped[Language] = mapped_column(Enum(Language), default=Language.en)
    business_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    agent_name: Mapped[str] = mapped_column(String(100), default="Alex")
    custom_instructions: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    knowledge_base: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)  # FAQs, menu, policies
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
