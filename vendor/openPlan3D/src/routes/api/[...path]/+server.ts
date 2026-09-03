/**
 * Same-origin proxy to the Python design service.
 *
 * Exists so the browser only ever talks to one host. CP-SAT and shapely have to
 * run in Python, but that is our problem, not the client's: no CORS, no second
 * base URL to configure, no separate deploy for the front end.
 */
import type { RequestHandler } from '@sveltejs/kit';

const SERVICE = process.env.FPEVAL_SERVICE ?? 'http://127.0.0.1:8099';

async function forward(request: Request, path: string): Promise<Response> {
  const url = new URL(request.url);
  const target = `${SERVICE}/api/${path}${url.search}`;
  const init: RequestInit = {
    method: request.method,
    headers: { 'content-type': request.headers.get('content-type') ?? 'application/json' },
  };
  if (request.method !== 'GET' && request.method !== 'HEAD') {
    init.body = await request.text();
  }
  try {
    const res = await fetch(target, init);
    return new Response(res.body, {
      status: res.status,
      headers: { 'content-type': res.headers.get('content-type') ?? 'application/json' },
    });
  } catch (e) {
    // A dead service is the common case in dev; say so plainly rather than
    // surfacing an opaque 500 in the editor.
    return new Response(
      JSON.stringify({
        error: 'design service unreachable',
        service: SERVICE,
        hint: 'start it with: uv run --with fastapi --with uvicorn --with shapely --with numpy --with ortools uvicorn service.app:app --port 8099',
        detail: String(e),
      }),
      { status: 503, headers: { 'content-type': 'application/json' } },
    );
  }
}

export const GET: RequestHandler = ({ request, params }) => forward(request, params.path ?? '');
export const POST: RequestHandler = ({ request, params }) => forward(request, params.path ?? '');
