from __future__ import annotations

import json
import os
import re
import base64
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Optional, cast


ROOT = Path(__file__).resolve().parent
BACKUP_AI = ROOT / "backup_ai"
ASSET_ROOT = BACKUP_AI / "prod-mc-asset-server" / "_"
AUTH_PATH = BACKUP_AI / "prod-mc-auth.json"

OUT_JSONL = ROOT / "historial_chronologico.jsonl"
OUT_HTML = ROOT / "historial_chronologico.html"


ISO_TS_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})"
)


def _dt_from_iso(ts: str) -> Optional[datetime]:
    ts = ts.strip()
    if not ts:
        return None
    if ts.endswith("Z"):
        ts = ts[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(ts)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _dt_from_mtime(path: Path) -> datetime:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)


def _detect_file_kind(path: Path) -> tuple[str, dict[str, Any]]:
    try:
        with path.open("rb") as f:
            head = f.read(32)
    except OSError:
        return "unreadable", {}

    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        if len(head) >= 24:
            width = int.from_bytes(head[16:20], "big")
            height = int.from_bytes(head[20:24], "big")
            return "png", {"width": width, "height": height}
        return "png", {}

    if len(head) >= 12 and head[0:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp", {}

    if len(head) >= 3 and head[0:3] == b"\xFF\xD8\xFF":
        return "jpeg", {}

    if head.startswith(b"GIF87a") or head.startswith(b"GIF89a"):
        return "gif", {}

    if head.startswith(b"%PDF-"):
        return "pdf", {}

    if head.startswith(b"{") or head.startswith(b"["):
        return "json_or_text", {}

    return "binary", {}


@dataclass(frozen=True)
class Evento:
    ts: datetime
    tipo: str
    fuente: str
    datos: dict[str, Any]


def _is_sensitive_key(key: str) -> bool:
    k = key.strip().lower()
    if not k:
        return False
    needles = (
        "token",
        "secret",
        "password",
        "passwd",
        "api_key",
        "apikey",
        "access_key",
        "private_key",
        "clientsecret",
        "refresh",
        "authorization",
        "bearer",
    )
    return any(n in k for n in needles)


def _redact_jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            if isinstance(k, str) and _is_sensitive_key(k):
                out[k] = "<redacted>"
            else:
                out[k] = _redact_jsonable(v)
        return out
    if isinstance(value, list):
        return [_redact_jsonable(v) for v in value]
    if isinstance(value, tuple):
        return [_redact_jsonable(v) for v in value]
    return value


def _evento_to_json(e: Evento) -> dict[str, Any]:
    return {
        "timestamp": e.ts.astimezone(timezone.utc).isoformat(),
        "type": e.tipo,
        "source": e.fuente,
        "data": cast(dict[str, Any], _redact_jsonable(e.datos)),
    }


def _segmentar_sesiones(
    eventos: list[Evento], max_gap_horas: float = 6.0
) -> list[int]:
    if not eventos:
        return []
    max_gap = timedelta(hours=max_gap_horas)
    sesiones: list[int] = []
    actual = 1
    ultimo = eventos[0].ts
    for e in eventos:
        if e.ts - ultimo > max_gap:
            actual += 1
        sesiones.append(actual)
        ultimo = e.ts
    return sesiones


def _as_rel(path: Path) -> str:
    return str(path.relative_to(ROOT)).replace(os.sep, "/")


def _split_txt_into_items(path: Path) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    rel = _as_rel(path)

    buf: list[tuple[int, str]] = []

    def flush_buf() -> None:
        nonlocal buf
        if not buf:
            return
        lines = [t for _, t in buf]
        while lines and lines[0].strip() == "":
            lines.pop(0)
        while lines and lines[-1].strip() == "":
            lines.pop()
        if not lines:
            buf = []
            return

        line_start = buf[0][0]
        line_end = buf[-1][0]
        text = "\n".join(lines)
        items.append(
            {
                "dt": None,
                "kind": "text",
                "source": rel,
                "line_start": line_start,
                "line_end": line_end,
                "text": text,
            }
        )
        buf = []

    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            for line_no, raw in enumerate(f, start=1):
                line = raw.rstrip("\n")
                s = line.strip()
                if s.startswith("{") and s.endswith("}"):
                    try:
                        obj = json.loads(s)
                    except json.JSONDecodeError:
                        buf.append((line_no, line))
                        continue
                    if isinstance(obj, dict) and isinstance(obj.get("timestamp"), str):
                        dt = _dt_from_iso(cast(str, obj.get("timestamp")))
                        if dt is not None:
                            flush_buf()
                            items.append(
                                {
                                    "dt": dt,
                                    "kind": "json",
                                    "source": rel,
                                    "line": line_no,
                                    "obj": obj,
                                }
                            )
                            continue
                buf.append((line_no, line))
    except OSError:
        return []

    flush_buf()
    return items


def _assign_datetimes_to_items(path: Path, items: list[dict[str, Any]]) -> list[Evento]:
    if not items:
        return []

    rel = _as_rel(path)
    anchors: list[tuple[int, datetime]] = []
    for i, it in enumerate(items):
        if isinstance(it.get("dt"), datetime):
            anchors.append((i, cast(datetime, it["dt"])))

    out: list[Evento] = []
    base = _dt_from_mtime(path)

    def emit(it: dict[str, Any], dt: datetime, micro_offset: int) -> None:
        ts = dt + timedelta(microseconds=micro_offset)
        if it["kind"] == "json":
            out.append(
                Evento(
                    ts=ts,
                    tipo="log_json",
                    fuente=rel,
                    datos={"line": it["line"], "obj": it["obj"]},
                )
            )
        else:
            out.append(
                Evento(
                    ts=ts,
                    tipo="chat_text",
                    fuente=rel,
                    datos={
                        "line_start": it["line_start"],
                        "line_end": it["line_end"],
                        "text": it["text"],
                    },
                )
            )

    if not anchors:
        for idx, it in enumerate(items):
            emit(it, base, idx)
        return out

    first_i, first_dt = anchors[0]
    pre = [it for it in items[:first_i] if it["kind"] == "text"]
    for j, it in enumerate(pre):
        emit(it, first_dt - timedelta(seconds=1), j)

    for a_idx, (ai, adt) in enumerate(anchors):
        emit(items[ai], adt, 0)
        bi = anchors[a_idx + 1][0] if a_idx + 1 < len(anchors) else None
        bdt = anchors[a_idx + 1][1] if a_idx + 1 < len(anchors) else None

        mid_items = items[ai + 1 : bi] if bi is not None else items[ai + 1 :]
        mid_text = [it for it in mid_items if it["kind"] == "text"]
        if not mid_text:
            continue

        if bdt is None:
            for j, it in enumerate(mid_text, start=1):
                emit(it, adt, j)
            continue

        span = max((bdt - adt).total_seconds(), 0.0)
        for j, it in enumerate(mid_text, start=1):
            frac = j / (len(mid_text) + 1)
            emit(it, adt + timedelta(seconds=span * frac), j)

    return out


def _iter_auth_events() -> Iterable[Evento]:
    if not AUTH_PATH.exists():
        return []
    try:
        raw = AUTH_PATH.read_text(encoding="utf-8", errors="replace")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError):
        return []

    sessions = data.get("sessions")
    if not isinstance(sessions, list):
        return []

    out: list[Evento] = []
    for i, sess in enumerate(sessions):
        if not isinstance(sess, dict):
            continue
        create_dt = _dt_from_iso(str(sess.get("createTime", "")).strip())
        if create_dt is None:
            continue

        out.append(
            Evento(
                ts=create_dt,
                tipo="auth_session",
                fuente=str(AUTH_PATH.relative_to(ROOT)).replace(os.sep, "/"),
                datos={
                    "index": i,
                    "status": sess.get("status"),
                    "createTime": sess.get("createTime"),
                    "lastAuthTime": sess.get("lastAuthTime"),
                    "expirationTime": sess.get("expirationTime"),
                },
            )
        )
    return out


def _iter_asset_events(max_assets: Optional[int] = None) -> Iterable[Evento]:
    if not ASSET_ROOT.exists():
        return []
    count = 0
    out: list[Evento] = []
    for d in sorted([p for p in ASSET_ROOT.iterdir() if p.is_dir()]):
        if max_assets is not None and count >= max_assets:
            break

        content = d / "content"
        preview = d / "preview-image"
        main = preview if preview.exists() else content
        if not main.exists():
            continue

        dt = _dt_from_mtime(main)
        kind, meta = _detect_file_kind(main)
        rel = main.relative_to(ROOT).as_posix()

        datos: dict[str, Any] = {
            "asset_id": d.name,
            "path": rel,
            "kind": kind,
            "size_bytes": main.stat().st_size,
        }
        datos.update(meta)

        if content.exists() and preview.exists() and content != main:
            datos["content_path"] = content.relative_to(ROOT).as_posix()
            datos["preview_path"] = preview.relative_to(ROOT).as_posix()

        out.append(
            Evento(
                ts=dt,
                tipo="asset",
                fuente="backup_ai/prod-mc-asset-server",
                datos=datos,
            )
        )
        count += 1
    return out


def _html_escape(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def _mime_from_kind(kind: str) -> Optional[str]:
    if kind == "png":
        return "image/png"
    if kind == "webp":
        return "image/webp"
    if kind == "jpeg":
        return "image/jpeg"
    if kind == "gif":
        return "image/gif"
    if kind == "pdf":
        return "application/pdf"
    return None


def _file_to_data_uri(path: Path, mime: str, max_bytes: int = 6_000_000) -> Optional[str]:
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if len(data) > max_bytes:
        return None
    b64 = base64.b64encode(data).decode("ascii")
    return f"data:{mime};base64,{b64}"


def _write_outputs(eventos: list[Evento]) -> None:
    def _sort_key(e: Evento) -> tuple[Any, ...]:
        line = e.datos.get("line")
        if line is None:
            line = e.datos.get("line_start", 0)
        asset = e.datos.get("asset_id", "")
        return (e.ts, e.fuente, e.tipo, line, asset)

    eventos_sorted = sorted(eventos, key=_sort_key)
    sesiones = _segmentar_sesiones(eventos_sorted)

    stats_por_sesion: dict[int, dict[str, Any]] = {}
    for e, s in zip(eventos_sorted, sesiones):
        info = stats_por_sesion.setdefault(
            s,
            {
                "inicio": e.ts,
                "fin": e.ts,
                "cuenta": 0,
                "tipos": Counter(),
                "fuentes": Counter(),
            },
        )
        if e.ts < info["inicio"]:
            info["inicio"] = e.ts
        if e.ts > info["fin"]:
            info["fin"] = e.ts
        info["cuenta"] += 1
        info["tipos"][e.tipo] += 1
        info["fuentes"][e.fuente] += 1

    with OUT_JSONL.open("w", encoding="utf-8") as f:
        for e in eventos_sorted:
            f.write(json.dumps(_evento_to_json(e), ensure_ascii=False))
            f.write("\n")

    parts: list[str] = []
    parts.append("<!doctype html>")
    parts.append("<meta charset='utf-8'>")
    parts.append("<title>Historial cronológico</title>")
    parts.append(
        "<style>"
        "body{font-family:system-ui,Segoe UI,Arial,sans-serif;margin:16px;background:#0b0b0b;color:#eaeaea;}"
        "a{color:#8ab4ff;}"
        ".toolbar{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:12px 0;}"
        "input[type='search']{padding:10px 12px;border:1px solid #2a2a2a;border-radius:10px;min-width:320px;background:#111;color:#eee;}"
        "select,button{padding:10px 12px;border:1px solid #2a2a2a;border-radius:10px;background:#111;color:#eee;}"
        "button{cursor:pointer;}"
        ".meta{color:#bdbdbd;font-size:12px;margin-bottom:8px;}"
        ".session{margin:18px 0;}"
        ".bubble{max-width:980px;border:1px solid #2a2a2a;border-radius:14px;padding:12px 14px;margin:10px 0;background:#0f0f10;}"
        ".hdr{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-bottom:8px;font-size:12px;color:#bdbdbd;}"
        ".tag{font-weight:600;color:#eaeaea;}"
        ".content{white-space:pre-wrap;word-break:break-word;}"
        ".json{background:#0b0b0b;border:1px solid #2a2a2a;border-radius:12px;padding:10px;overflow:auto;}"
        "details>summary{cursor:pointer;list-style:none;}"
        "details>summary::-webkit-details-marker{display:none;}"
        "img{max-width:100%;height:auto;border-radius:12px;border:1px solid #2a2a2a;display:block;}"
        ".file{display:flex;gap:10px;align-items:center;flex-wrap:wrap;}"
        ".pill{display:inline-block;padding:4px 8px;border-radius:999px;border:1px solid #2a2a2a;background:#111;color:#ddd;font-size:12px;}"
        ".assetImg{cursor:zoom-in;}"
        ".modal{position:fixed;inset:0;background:rgba(0,0,0,.78);display:none;align-items:center;justify-content:center;padding:18px;z-index:9999;}"
        ".modal.open{display:flex;}"
        ".modalInner{max-width:min(1200px,96vw);max-height:92vh;}"
        ".modalInner img{max-height:92vh;cursor:zoom-out;}"
        "</style>"
    )
    parts.append(f"<h1>Historial cronológico (modo chat) · {len(eventos_sorted)} eventos</h1>")
    if eventos_sorted:
        start = eventos_sorted[0].ts.astimezone(timezone.utc).isoformat()
        end = eventos_sorted[-1].ts.astimezone(timezone.utc).isoformat()
        parts.append(f"<div class='meta'>Rango: {_html_escape(start)} → {_html_escape(end)}</div>")
    if stats_por_sesion:
        parts.append("<div class='meta'>Sesiones detectadas:</div>")
        parts.append("<ul class='meta'>")
        for s in sorted(stats_por_sesion):
            st = stats_por_sesion[s]
            ini = _html_escape(st["inicio"].astimezone(timezone.utc).isoformat())
            fin = _html_escape(st["fin"].astimezone(timezone.utc).isoformat())
            parts.append(
                f"<li><a href='#sesion-{s}'>Sesión {s}</a>: "
                f"{ini} → {fin} · {st['cuenta']} eventos</li>"
            )
        parts.append("</ul>")
    parts.append(
        "<div class='toolbar'>"
        "<input id='q' type='search' placeholder='Buscar en todo el historial…'>"
        "<select id='typeFilter'><option value=''>Tipo: todos</option></select>"
        "<select id='sourceFilter'><option value=''>Fuente: todas</option></select>"
        "<button id='expandAll' type='button'>Expandir todo</button>"
        "<button id='collapseAll' type='button'>Colapsar todo</button>"
        "<span id='count' class='meta'></span>"
        "</div>"
    )
    parts.append("<div id='modal' class='modal' role='dialog' aria-modal='true'><div class='modalInner'><img id='modalImg' alt=''></div></div>")

    sesion_actual = None
    for idx, e in enumerate(eventos_sorted):
        s_id = sesiones[idx]
        if s_id != sesion_actual:
            sesion_actual = s_id
            st = stats_por_sesion[sesion_actual]
            ini = _html_escape(st["inicio"].astimezone(timezone.utc).isoformat())
            fin = _html_escape(st["fin"].astimezone(timezone.utc).isoformat())
            parts.append(
                f"<div class='session'><h2 id='sesion-{sesion_actual}'>Sesión {sesion_actual} "
                f"({ini} → {fin}, {st['cuenta']} eventos)</h2></div>"
            )

        ts = _html_escape(e.ts.astimezone(timezone.utc).isoformat())
        tipo = _html_escape(e.tipo)
        fuente = _html_escape(e.fuente)
        parts.append(f"<div class='bubble event' data-type='{tipo}' data-source='{fuente}'>")
        parts.append(f"<div class='hdr'><span class='tag'>{tipo}</span><span>{ts}</span><span>{fuente}</span></div>")

        if e.tipo == "chat_text":
            text = _html_escape(str(e.datos.get("text", "")))
            parts.append(f"<div class='content'>{text}</div>")
            parts.append("</div>")
            continue

        if e.tipo == "asset":
            kind = str(e.datos.get("kind", "binary"))
            rel_path = str(e.datos.get("path", ""))
            size = int(e.datos.get("size_bytes") or 0)
            asset_id = _html_escape(str(e.datos.get("asset_id", "")))
            mime = _mime_from_kind(kind)
            is_img = kind in {"png", "webp", "jpeg", "gif"}
            data_uri = None
            if mime and rel_path:
                data_uri = _file_to_data_uri(ROOT / rel_path, mime=mime)
            if is_img and data_uri:
                safe_src = _html_escape(data_uri)
                parts.append(f"<div><img class='assetImg' loading='lazy' src='{safe_src}' data-full='{safe_src}' alt='asset {asset_id}'></div>")
                parts.append(
                    "<div class='file' style='margin-top:10px'>"
                    f"<span class='pill'>asset {asset_id}</span>"
                    f"<span class='pill'>{_html_escape(kind)}</span>"
                    f"<span class='pill'>{size} bytes</span>"
                    "</div>"
                )
                parts.append("</div>")
                continue
            if is_img and rel_path:
                safe_path = _html_escape(rel_path)
                parts.append(f"<div><img class='assetImg' loading='lazy' src='{safe_path}' data-full='{safe_path}' alt='asset {asset_id}'></div>")
                parts.append(
                    "<div class='file' style='margin-top:10px'>"
                    f"<span class='pill'>asset {asset_id}</span>"
                    f"<span class='pill'>{_html_escape(kind)}</span>"
                    f"<span class='pill'>{size} bytes</span>"
                    "</div>"
                )
                parts.append("</div>")
                continue

            shown_path = _html_escape(rel_path) if rel_path else ""
            parts.append(
                "<div class='file'>"
                f"<span class='pill'>asset {asset_id}</span>"
                f"<span class='pill'>{_html_escape(kind)}</span>"
                f"<span class='pill'>{size} bytes</span>"
                + "</div>"
            )
            parts.append("</div>")
            continue

        if e.tipo == "auth_session":
            safe = _redact_jsonable(e.datos)
            parts.append(f"<div class='json'>{_html_escape(json.dumps(safe, ensure_ascii=False, indent=2))}</div>")
            parts.append("</div>")
            continue

        if e.tipo == "log_json":
            obj = e.datos.get("obj")
            summary = ""
            if isinstance(obj, dict):
                if "trade_id" in obj and "action" in obj:
                    summary = f"trade_id={obj.get('trade_id')} action={obj.get('action')} symbol={obj.get('symbol')}"
                elif "last_price" in obj and "volume_24h" in obj:
                    summary = f"ticker last_price={obj.get('last_price')} volume_24h={obj.get('volume_24h')} symbol={obj.get('symbol','')}"
                elif all(k in obj for k in ("open", "high", "low", "close")):
                    summary = f"kline o={obj.get('open')} h={obj.get('high')} l={obj.get('low')} c={obj.get('close')} v={obj.get('volume')}"
                elif "bids" in obj and "asks" in obj:
                    summary = f"orderbook bids={len(obj.get('bids') or [])} asks={len(obj.get('asks') or [])}"
                elif any(k in obj for k in ("combined", "ild", "egm", "rol", "pio")):
                    summary = "indicadores " + " ".join([k for k in ("combined", "ild", "egm", "rol", "pio") if k in obj])
            if summary:
                parts.append(f"<div class='meta'>{_html_escape(summary)}</div>")
            parts.append("<details>")
            parts.append("<summary class='meta'>Ver JSON</summary>")
            safe = _redact_jsonable(e.datos)
            parts.append(f"<div class='json'>{_html_escape(json.dumps(safe, ensure_ascii=False, indent=2))}</div>")
            parts.append("</details>")
            parts.append("</div>")
            continue

        safe = _redact_jsonable(e.datos)
        parts.append(f"<div class='json'>{_html_escape(json.dumps(safe, ensure_ascii=False, indent=2))}</div>")
        parts.append("</div>")

    parts.append(
        "<script>"
        "const q=document.getElementById('q');"
        "const typeFilter=document.getElementById('typeFilter');"
        "const sourceFilter=document.getElementById('sourceFilter');"
        "const events=[...document.querySelectorAll('.event')];"
        "const count=document.getElementById('count');"
        "const modal=document.getElementById('modal');"
        "const modalImg=document.getElementById('modalImg');"
        "const uniq=(arr)=>[...new Set(arr)].sort((a,b)=>a.localeCompare(b));"
        "uniq(events.map(e=>e.dataset.type)).forEach(t=>{const o=document.createElement('option'); o.value=t; o.textContent=`Tipo: ${t}`; typeFilter.appendChild(o);});"
        "uniq(events.map(e=>e.dataset.source)).forEach(s=>{const o=document.createElement('option'); o.value=s; o.textContent=`Fuente: ${s}`; sourceFilter.appendChild(o);});"
        "function openModal(src){ modal.classList.add('open'); modalImg.src=src; }"
        "function closeModal(){ modal.classList.remove('open'); modalImg.src=''; }"
        "document.addEventListener('click',(ev)=>{"
        "  const img=ev.target.closest?.('img.assetImg');"
        "  if(img){ ev.preventDefault(); openModal(img.dataset.full||img.src); return; }"
        "  if(ev.target===modal || ev.target===modalImg){ closeModal(); }"
        "});"
        "document.addEventListener('keydown',(ev)=>{ if(ev.key==='Escape') closeModal(); });"
        "function apply(){"
        "  const qq=(q.value||'').toLowerCase().trim();"
        "  const tf=typeFilter.value; const sf=sourceFilter.value;"
        "  let shown=0;"
        "  for(const el of events){"
        "    const okType=!tf || el.dataset.type===tf;"
        "    const okSource=!sf || el.dataset.source===sf;"
        "    let okText=true;"
        "    if(qq){ okText=el.textContent.toLowerCase().includes(qq); }"
        "    const ok=okType && okSource && okText;"
        "    el.style.display= ok ? '' : 'none';"
        "    if(ok) shown++;"
        "  }"
        "  count.textContent=`Mostrando ${shown} / ${events.length}`;"
        "}"
        "q.addEventListener('input', apply);"
        "typeFilter.addEventListener('change', apply);"
        "sourceFilter.addEventListener('change', apply);"
        "document.getElementById('expandAll').addEventListener('click', ()=>{events.forEach(e=>e.querySelector('details')?.setAttribute('open',''));});"
        "document.getElementById('collapseAll').addEventListener('click', ()=>{events.forEach(e=>e.querySelector('details')?.removeAttribute('open'));});"
        "apply();"
        "</script>"
    )

    OUT_HTML.write_text("\n".join(parts), encoding="utf-8")


def main() -> int:
    eventos: list[Evento] = []

    txt_files = [
        p
        for p in ROOT.rglob("*.txt")
        if p.is_file()
        and BACKUP_AI not in p.parents
        and p.name not in {OUT_HTML.name, OUT_JSONL.name}
    ]
    for p in sorted(txt_files):
        items = _split_txt_into_items(p)
        eventos.extend(_assign_datetimes_to_items(p, items))
    eventos.extend(list(_iter_auth_events()))
    eventos.extend(list(_iter_asset_events()))

    _write_outputs(eventos)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
