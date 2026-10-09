import streamlit as st
from utils.data import get_client, new_client


@st.cache_resource
def _get_admin_client():
    """Service role client — bypasses RLS for admin writes."""
    key = st.secrets.get("supabase_service_key") or st.secrets["supabase_key"]
    return new_client(key)


@st.cache_data(ttl=300)
def get_research(status_filter=None):
    sb = get_client()
    query = sb.table("research").select("*").order("published_at", desc=True)
    if status_filter:
        query = query.eq("status", status_filter)
    return query.execute().data


def upsert_research(data: dict):
    sb = _get_admin_client()
    if "id" in data and data["id"]:
        record_id = data["id"]
        payload = {k: v for k, v in data.items() if k != "id"}
        sb.table("research").update(payload).eq("id", record_id).execute()
    else:
        data.pop("id", None)
        sb.table("research").insert(data).execute()


def delete_research(research_id: int):
    sb = _get_admin_client()
    sb.table("research").delete().eq("id", research_id).execute()


def upload_pdf(file_bytes: bytes, filename: str) -> str:
    sb = _get_admin_client()
    path = filename
    sb.storage.from_("research-docs").upload(
        path, file_bytes, {"content-type": "application/pdf", "upsert": "true"}
    )
    return sb.storage.from_("research-docs").get_public_url(path)
