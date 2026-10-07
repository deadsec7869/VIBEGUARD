"""Discovery: deterministic crawl producing a compact AppMap. No LLM."""
from collections import deque
from urllib.parse import urljoin, urlparse

from .models import AppMap, Element, Page

# Spec routes: probed even if nothing links to them (protected pages usually aren't linked).
COMMON_PATHS = ["/login", "/register", "/dashboard", "/projects", "/tasks",
                "/team", "/settings", "/admin"]

EXTRACT_JS = """() => {
  const vis = el => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
  const clean = s => (s || "").replace(/\\s+/g, " ").trim().slice(0, 80);
  const out = [];
  document.querySelectorAll("a[href], button, input, select, textarea, [role=button]").forEach(el => {
    if (!vis(el)) return;
    const tag = el.tagName.toLowerCase();
    const type = el.getAttribute("type");
    if (tag === "input" && type === "hidden") return;
    const isBtn = tag === "button" || el.getAttribute("role") === "button" ||
                  (tag === "input" && ["submit", "button"].includes(type));
    const role = tag === "a" ? "link" : isBtn ? "button" : "field";
    let name = el.getAttribute("aria-label") || "";
    if (!name && el.labels && el.labels[0]) name = el.labels[0].innerText;
    if (!name) {
      if (tag === "input" && isBtn) name = el.value;
      else if (tag === "input" || tag === "textarea" || tag === "select") name = el.placeholder || el.name || "";
      else name = el.innerText;
    }
    name = clean(name);
    if (!name) return;
    const target = role === "field" ? "label:" + name : "role:" + role + ":" + name;
    out.push({tag, type, role, name, target, href: tag === "a" ? el.getAttribute("href") : null});
  });
  return out;
}"""


def _same_origin_path(href: str, base: str, origin: str) -> str | None:
    u = urlparse(urljoin(base, href))
    if u.netloc != origin or u.scheme not in ("http", "https"):
        return None
    return u.path or "/"


async def discover(browser, base_url: str, emit, max_pages: int = 15) -> AppMap:
    origin = urlparse(base_url).netloc
    ctx = await browser.new_context(viewport={"width": 1280, "height": 800})
    page = await ctx.new_page()
    queue = deque(["/"] + COMMON_PATHS)
    queued = set(queue)
    pages: list[Page] = []
    seen_final: set[str] = set()
    try:
        while queue and len(pages) < max_pages:
            path = queue.popleft()
            try:
                resp = await page.goto(base_url + path, wait_until="load", timeout=10000)
            except Exception as e:
                emit("discovery", f"{path}: unreachable ({type(e).__name__})", "warn")
                continue
            status = resp.status if resp else 0
            if status == 0 or (status >= 400 and path != "/"):
                continue
            final_path = urlparse(page.url).path or "/"
            redirected_to_known = final_path != path and final_path in seen_final
            elements, links, text = [], [], ""
            if not redirected_to_known:
                raw = await page.evaluate(EXTRACT_JS)
                elements = [Element(**r) for r in raw]
                for r in raw:
                    if r["href"]:
                        p2 = _same_origin_path(r["href"], page.url, origin)
                        if p2:
                            links.append(p2)
                            if p2 not in queued:
                                queued.add(p2)
                                queue.append(p2)
                text = (await page.inner_text("body"))[:400]
            seen_final.add(final_path)
            pages.append(Page(path=path, final_path=final_path, status=status,
                              title=await page.title(), elements=elements,
                              links=sorted(set(links)), text_excerpt=text))
            note = f" -> redirected to {final_path}" if final_path != path else ""
            emit("discovery", f"{path} [{status}] {len(elements)} elements{note}")
    finally:
        await ctx.close()
    return AppMap(base_url=base_url, pages=pages)
