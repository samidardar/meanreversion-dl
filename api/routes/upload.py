"""
CSV/Excel upload endpoint.
Parses file, creates Campaign + Contact records.
Accepted columns: first_name, last_name, phone, reason_for_call, language (optional)
Any extra columns are stored in contact.custom_data.
"""
import io
import uuid
import logging
from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession
import pandas as pd
from db.database import get_db
from db.models import Campaign, Contact, Language
from api.security import validate_upload, sanitize_string

router = APIRouter()
logger = logging.getLogger(__name__)

REQUIRED_COLUMNS = {"first_name", "last_name", "phone"}
KNOWN_COLUMNS = {"first_name", "last_name", "phone", "reason_for_call", "language"}

# Hard limits
MAX_CONTACTS_PER_UPLOAD = 10_000


def _normalize_phone(phone: str) -> str:
    import re
    digits = re.sub(r"\D", "", str(phone))
    if len(digits) == 10:
        return f"+1{digits}"
    if not str(phone).startswith("+"):
        return f"+{digits}"
    return str(phone)


def _parse_language(val) -> Language:
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
    # Validate and read file with security checks
    content = await validate_upload(file)
    filename = file.filename or ""

    # Sanitize text inputs
    campaign_name = sanitize_string(campaign_name, max_length=200)
    task_type = sanitize_string(task_type, max_length=50)

    if not campaign_name:
        raise HTTPException(status_code=422, detail="campaign_name is required")

    allowed_task_types = {"delivery", "survey", "reminder", "restaurant", "hotel", "support", "custom"}
    if task_type not in allowed_task_types:
        raise HTTPException(status_code=422, detail=f"Invalid task_type. Allowed: {allowed_task_types}")

    try:
        if filename.lower().endswith(".csv"):
            df = pd.read_csv(io.BytesIO(content))
        elif filename.lower().endswith((".xlsx", ".xls")):
            df = pd.read_excel(io.BytesIO(content))
        else:
            raise HTTPException(status_code=400, detail="Only CSV and Excel (.xlsx/.xls) files are supported.")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not parse file: {e}")

    if len(df) > MAX_CONTACTS_PER_UPLOAD:
        raise HTTPException(
            status_code=413,
            detail=f"Too many contacts. Maximum {MAX_CONTACTS_PER_UPLOAD} per upload.",
        )

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

    contacts_created = 0
    errors = []
    extra_columns = [c for c in df.columns if c not in KNOWN_COLUMNS]

    for idx, row in df.iterrows():
        try:
            raw_phone = str(row.get("phone", "")).strip()
            if not raw_phone:
                errors.append({"row": idx + 2, "error": "Empty phone number"})
                continue
            phone = _normalize_phone(raw_phone)

            first_name = sanitize_string(str(row.get("first_name", "")), 100)
            last_name = sanitize_string(str(row.get("last_name", "")), 100)

            if not first_name or not last_name:
                errors.append({"row": idx + 2, "error": "Missing first or last name"})
                continue

            custom_data = {}
            for col in extra_columns:
                val = row.get(col)
                if pd.notna(val):
                    custom_data[col] = sanitize_string(str(val), 500)

            reason_raw = row.get("reason_for_call", "")
            reason = sanitize_string(str(reason_raw), 500) if pd.notna(reason_raw) else None

            contact = Contact(
                id=str(uuid.uuid4()),
                campaign_id=campaign_id,
                first_name=first_name,
                last_name=last_name,
                phone=phone,
                reason_for_call=reason,
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
        "errors": errors[:20],  # cap error list at 20
        "message": f"Campaign created with {contacts_created} contacts. Start dialing with POST /campaigns/{campaign_id}/start",
    }
