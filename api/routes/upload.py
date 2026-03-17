"""
CSV/Excel upload endpoint.
Parses file, creates Campaign + Contact records.
Accepted columns: first_name, last_name, phone, reason_for_call, language (optional)
Any extra columns are stored in contact.custom_data.
"""
import io
import uuid
import logging
from typing import Optional
from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession
import pandas as pd
from db.database import get_db
from db.models import Campaign, Contact, Language

router = APIRouter()
logger = logging.getLogger(__name__)

REQUIRED_COLUMNS = {"first_name", "last_name", "phone"}
KNOWN_COLUMNS = {"first_name", "last_name", "phone", "reason_for_call", "language"}


def _normalize_phone(phone: str) -> str:
    """Best-effort E.164 normalization."""
    import re
    digits = re.sub(r"\D", "", str(phone))
    if len(digits) == 10:
        return f"+1{digits}"  # assume North American
    if not digits.startswith("+"):
        return f"+{digits}"
    return digits


def _parse_language(val: Optional[str]) -> Language:
    if val and str(val).strip().lower() in ("fr", "french", "français"):
        return Language.fr
    return Language.en


@router.post("/upload", summary="Upload CSV or Excel contact list")
async def upload_contacts(
    file: UploadFile = File(...),
    campaign_name: str = Form(...),
    task_type: str = Form(..., description="delivery | survey | reminder | custom"),
    db: AsyncSession = Depends(get_db),
):
    """
    Upload a CSV or Excel file with contact list for outbound campaign.

    Required columns: first_name, last_name, phone
    Optional columns: reason_for_call, language (en/fr)
    Any extra columns are preserved in custom_data.
    """
    filename = file.filename or ""
    content = await file.read()

    try:
        if filename.endswith(".csv"):
            df = pd.read_csv(io.BytesIO(content))
        elif filename.endswith((".xlsx", ".xls")):
            df = pd.read_excel(io.BytesIO(content))
        else:
            raise HTTPException(status_code=400, detail="Only CSV and Excel (.xlsx/.xls) files are supported.")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not parse file: {e}")

    # Normalize column names
    df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]

    missing = REQUIRED_COLUMNS - set(df.columns)
    if missing:
        raise HTTPException(
            status_code=422,
            detail=f"Missing required columns: {missing}. File has: {list(df.columns)}",
        )

    # Create campaign
    campaign_id = str(uuid.uuid4())
    campaign = Campaign(
        id=campaign_id,
        name=campaign_name,
        task_type=task_type,
        total_contacts=len(df),
    )
    db.add(campaign)
    await db.flush()

    # Create contacts
    contacts_created = 0
    errors = []

    extra_columns = [c for c in df.columns if c not in KNOWN_COLUMNS]

    for idx, row in df.iterrows():
        try:
            phone = _normalize_phone(row["phone"])
            custom_data = {col: str(row[col]) for col in extra_columns if pd.notna(row.get(col))}

            contact = Contact(
                id=str(uuid.uuid4()),
                campaign_id=campaign_id,
                first_name=str(row["first_name"]).strip(),
                last_name=str(row["last_name"]).strip(),
                phone=phone,
                reason_for_call=str(row.get("reason_for_call", "")) if pd.notna(row.get("reason_for_call", "")) else None,
                language=_parse_language(row.get("language")),
                custom_data=custom_data if custom_data else None,
            )
            db.add(contact)
            contacts_created += 1
        except Exception as e:
            errors.append({"row": idx + 2, "error": str(e)})

    await db.commit()

    return {
        "campaign_id": campaign_id,
        "campaign_name": campaign_name,
        "task_type": task_type,
        "contacts_created": contacts_created,
        "errors": errors,
        "message": f"Campaign created. Start dialing with POST /campaigns/{campaign_id}/start",
    }
