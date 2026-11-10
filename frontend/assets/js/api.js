// Thin fetch wrapper: same-origin cookies, CSRF header on writes, uniform errors.

const UNSAFE = new Set(["POST", "PUT", "PATCH", "DELETE"]);

export class ApiError extends Error {
  constructor(status, code, message, details) {
    super(message);
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

function readCookie(name) {
  const match = document.cookie.split("; ").find((c) => c.startsWith(name + "="));
  return match ? decodeURIComponent(match.slice(name.length + 1)) : null;
}

export async function api(path, { method = "GET", body, redirectOn401 = true } = {}) {
  const headers = { Accept: "application/json" };
  const isForm = body instanceof FormData;
  if (body !== undefined && !isForm) headers["Content-Type"] = "application/json";
  if (UNSAFE.has(method)) {
    const csrf = readCookie("agencyos_csrf");
    if (csrf) headers["X-CSRF-Token"] = csrf;
  }

  let response;
  try {
    response = await fetch(`/api/v1${path}`, {
      method,
      headers,
      credentials: "same-origin",
      body: body === undefined ? undefined : isForm ? body : JSON.stringify(body),
    });
  } catch {
    throw new ApiError(0, "network_error", "Cannot reach the AgencyOS server. Is it running?");
  }

  if (response.status === 401 && redirectOn401) {
    window.location.href = "/login";
    throw new ApiError(401, "unauthenticated", "Session expired");
  }
  if (response.status === 204) return null;

  let data = null;
  try {
    data = await response.json();
  } catch {
    /* non-JSON body */
  }
  if (!response.ok) {
    const err = data?.error ?? {};
    throw new ApiError(
      response.status,
      err.code ?? "error",
      err.message ?? `Request failed (${response.status})`,
      err.details,
    );
  }
  return data;
}

// Render ApiError into a .form-error container (safe text only).
export function showFormError(container, error) {
  container.replaceChildren();
  container.append(document.createTextNode(error.message));
  if (Array.isArray(error.details) && error.details.length) {
    const ul = document.createElement("ul");
    for (const d of error.details) {
      const li = document.createElement("li");
      li.textContent = typeof d === "string" ? d : `${d.field ?? ""}: ${d.message ?? ""}`;
      ul.append(li);
    }
    container.append(ul);
  }
  container.classList.remove("hidden");
}
