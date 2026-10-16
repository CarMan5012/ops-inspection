from __future__ import annotations

from io import BytesIO
from pathlib import Path
from uuid import uuid4

from PIL import Image, UnidentifiedImageError

from app.db import connect
from app.settings import settings

BUILTIN_DIR = Path(__file__).resolve().parent / "static" / "images"
BUILTIN_NAMES = ("windows-taskbar-template.png", "1.png", "2.png", "3.png")
MAX_IMAGE_BYTES = 5 * 1024 * 1024


def upload_directory() -> Path:
    return settings.data_dir / "taskbar-templates"


def uploaded_path(template: dict) -> Path:
    name = str(template["stored_name"])
    if len(name) != 36 or not name.endswith(".png") or any(c not in "0123456789abcdef" for c in name[:-4]):
        raise ValueError("底栏文件名无效")
    return upload_directory() / name


def validate_image(blob: bytes) -> bytes:
    if not blob or len(blob) > MAX_IMAGE_BYTES:
        raise ValueError("底栏图片不能为空且不能超过 5 MB")
    try:
        with Image.open(BytesIO(blob)) as source:
            if source.format not in {"PNG", "JPEG", "WEBP"}:
                raise ValueError("只支持 PNG、JPEG 或 WebP 图片")
            width, height = source.size
            if not (800 <= width <= 8000 and 20 <= height <= 300 and width / height >= 10):
                raise ValueError("底栏图片应为宽幅横条：宽 800–8000 像素，高 20–300 像素，宽高比至少 10:1")
            source.load()
            image = source.convert("RGBA")
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError("无法读取底栏图片") from exc
    output = BytesIO()
    image.save(output, format="PNG", optimize=True)
    return output.getvalue()


def list_uploaded() -> list[dict]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM taskbar_templates ORDER BY id DESC").fetchall()
    return [dict(row) for row in rows]


def get_uploaded(template_id: int) -> dict | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM taskbar_templates WHERE id = ?", (template_id,)).fetchone()
    return dict(row) if row else None


def all_templates() -> list[dict]:
    defaults = [
        {"key": f"builtin:{name}", "name": f"默认底栏 {index}", "kind": "builtin", "original_name": name}
        for index, name in enumerate(BUILTIN_NAMES, 1)
        if (BUILTIN_DIR / name).is_file()
    ]
    uploaded = [
        {"key": f"custom:{row['id']}", "name": row["name"], "kind": "custom",
         "original_name": row["original_name"], "created_at": row["created_at"]}
        for row in list_uploaded()
    ]
    return defaults + uploaded


def selected_path(key: str) -> Path:
    if key.startswith("builtin:"):
        name = key.removeprefix("builtin:")
        if name not in BUILTIN_NAMES:
            raise ValueError("底栏模板不存在")
        path = BUILTIN_DIR / name
    elif key.startswith("custom:"):
        try:
            template_id = int(key.removeprefix("custom:"))
        except ValueError as exc:
            raise ValueError("底栏模板无效") from exc
        template = get_uploaded(template_id)
        if not template:
            raise ValueError("底栏模板不存在")
        path = uploaded_path(template)
    else:
        raise ValueError("底栏模板无效")
    if not path.is_file():
        raise ValueError("底栏图片文件不存在")
    return path


def create_uploaded(name: str, original_name: str, blob: bytes) -> dict:
    name = name.strip()
    if not name or len(name) > 100:
        raise ValueError("底栏名称长度需为 1 到 100 个字符")
    image = validate_image(blob)
    directory = upload_directory()
    directory.mkdir(parents=True, exist_ok=True)
    stored_name = f"{uuid4().hex}.png"
    path = directory / stored_name
    with path.open("xb") as file:
        file.write(image)
    try:
        with connect() as conn:
            cursor = conn.execute(
                "INSERT INTO taskbar_templates (name, stored_name, original_name) VALUES (?, ?, ?)",
                (name, stored_name, Path(original_name).name),
            )
            template_id = int(cursor.lastrowid)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return {"key": f"custom:{template_id}", "name": name, "kind": "custom", "original_name": Path(original_name).name}


def delete_uploaded(template_id: int) -> None:
    template = get_uploaded(template_id)
    if not template:
        raise ValueError("底栏模板不存在")
    with connect() as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM screenshot_items WHERE taskbar_template_key = ?", (f"custom:{template_id}",)
        ).fetchone()[0]
        if count:
            raise ValueError(f"有 {count} 个截图项正在使用此底栏，请先修改截图项")
        conn.execute("DELETE FROM taskbar_templates WHERE id = ?", (template_id,))
    uploaded_path(template).unlink(missing_ok=True)
