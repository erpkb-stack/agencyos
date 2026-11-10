from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.orm import Session

from app.config.settings import Settings, get_settings
from app.database.session import get_db
from app.models.identity import User
from app.schemas.auth import (
    ChangePasswordRequest,
    CreateUserRequest,
    DemoLoginStatus,
    LoginRequest,
    LoginResponse,
    MfaCodeRequest,
    MfaDisableRequest,
    MfaLoginRequest,
    UpdateUserRequest,
    UserOut,
)
from app.security.deps import (
    client_ip,
    enforce_login_rate_limit,
    get_current_user,
    get_session_token,
    require_admin,
)
from app.security.tokens import CSRF_COOKIE, SESSION_COOKIE, csrf_token_for
from app.services import auth_service
from app.services.auth_service import IssuedSession
from app.utils.errors import NotFoundError, PermissionDeniedError

router = APIRouter(prefix="/auth", tags=["auth"])


def _set_session_cookies(response: Response, issued: IssuedSession, settings: Settings) -> str:
    max_age = settings.session_ttl_hours * 3600
    csrf = csrf_token_for(issued.token, settings.secret_key)
    response.set_cookie(
        SESSION_COOKIE, issued.token, max_age=max_age, httponly=True,
        secure=settings.secure_cookies, samesite="lax", path="/",
    )
    # Readable by our JS so it can echo the value in the X-CSRF-Token header.
    response.set_cookie(
        CSRF_COOKIE, csrf, max_age=max_age, httponly=False,
        secure=settings.secure_cookies, samesite="strict", path="/",
    )
    return csrf


def _clear_cookies(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")


@router.post("/login", dependencies=[Depends(enforce_login_rate_limit)])
def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Step 1. Returns the session, or ``{"mfa_required": true, "mfa_token": …}`` when 2FA is on."""
    outcome = auth_service.login(
        db, settings, body.email, body.password,
        ip=client_ip(request), user_agent=request.headers.get("user-agent"),
    )
    if isinstance(outcome, auth_service.MfaChallenge):
        return {"mfa_required": True, "mfa_token": outcome.token}
    csrf = _set_session_cookies(response, outcome, settings)
    return {"mfa_required": False, **LoginResponse(user=UserOut.model_validate(outcome.user), csrf_token=csrf).model_dump()}


@router.post("/login/2fa", response_model=LoginResponse, dependencies=[Depends(enforce_login_rate_limit)])
def login_2fa(body: MfaLoginRequest, request: Request, response: Response, db: Session = Depends(get_db),
              settings: Settings = Depends(get_settings)) -> LoginResponse:
    """Step 2: the authenticator code (or a recovery code) for the user who passed step 1."""
    issued = auth_service.complete_mfa_login(db, settings, body.mfa_token, body.code, body.recovery_code,
                                             ip=client_ip(request), user_agent=request.headers.get("user-agent"))
    csrf = _set_session_cookies(response, issued, settings)
    return LoginResponse(user=UserOut.model_validate(issued.user), csrf_token=csrf)


@router.get("/demo-login/status", response_model=DemoLoginStatus)
def demo_login_status(settings: Settings = Depends(get_settings)) -> DemoLoginStatus:
    return DemoLoginStatus(enabled=settings.demo_login_enabled)


@router.post(
    "/demo-login", response_model=LoginResponse, dependencies=[Depends(enforce_login_rate_limit)]
)
def demo_login(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> LoginResponse:
    try:
        issued = auth_service.demo_login(
            db, settings, ip=client_ip(request), user_agent=request.headers.get("user-agent")
        )
    except PermissionDeniedError as exc:
        raise NotFoundError("Not found") from exc
    csrf = _set_session_cookies(response, issued, settings)
    return LoginResponse(user=UserOut.model_validate(issued.user), csrf_token=csrf)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Response:
    auth_service.logout(db, get_session_token(request), user, ip=client_ip(request))
    response.status_code = status.HTTP_204_NO_CONTENT
    _clear_cookies(response)
    return response


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)) -> User:
    return user


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(
    body: ChangePasswordRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Response:
    auth_service.change_password(
        db, user, body.current_password, body.new_password, keep_token=get_session_token(request)
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/users", response_model=list[UserOut])
def list_users(db: Session = Depends(get_db), _: User = Depends(require_admin)) -> list[User]:
    return auth_service.list_users(db)


@router.post("/users", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def create_user(
    body: CreateUserRequest,
    db: Session = Depends(get_db),
    actor: User = Depends(require_admin),
) -> User:
    return auth_service.create_user(
        db, email=body.email, full_name=body.full_name, password=body.password,
        role=body.role, actor=actor,
    )


@router.patch("/users/{user_id}", response_model=UserOut)
def update_user(user_id: int, body: UpdateUserRequest, db: Session = Depends(get_db),
                actor: User = Depends(require_admin)) -> User:
    """Activate/deactivate a user or change their role. Deactivation signs them out everywhere."""
    return auth_service.update_user(db, actor, user_id, is_active=body.is_active, role=body.role)


# -- two-factor authentication -----------------------------------------------------------------


@router.get("/2fa")
def mfa_status(user: User = Depends(get_current_user), settings: Settings = Depends(get_settings)) -> dict:
    return {"enabled": user.totp_enabled, "enabled_at": user.totp_enabled_at,
            "recovery_codes_left": len(user.recovery_codes or []) if user.totp_enabled else 0,
            "required": settings.require_2fa_for_admins and user.role.value in ("FOUNDER", "ADMIN")}


@router.post("/2fa/setup")
def mfa_setup(db: Session = Depends(get_db), user: User = Depends(get_current_user),
              settings: Settings = Depends(get_settings)) -> dict:
    """Start enrolment: returns the secret and a QR code. Not active until confirmed with a code."""
    return auth_service.start_mfa_setup(db, user, settings)


@router.post("/2fa/enable")
def mfa_enable(body: MfaCodeRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user),
               settings: Settings = Depends(get_settings)) -> dict:
    """Confirm with the first code. Returns recovery codes; they are shown only this once."""
    return {"recovery_codes": auth_service.enable_mfa(db, user, settings, body.code)}


@router.post("/2fa/disable", status_code=status.HTTP_204_NO_CONTENT)
def mfa_disable(body: MfaDisableRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user),
                settings: Settings = Depends(get_settings)) -> Response:
    auth_service.disable_mfa(db, user, settings, body.password, body.code, body.recovery_code)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/2fa/recovery-codes")
def mfa_recovery_codes(body: MfaCodeRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user),
                       settings: Settings = Depends(get_settings)) -> dict:
    return {"recovery_codes": auth_service.regenerate_recovery_codes(db, user, settings, body.code)}


# -- sessions ----------------------------------------------------------------------------------


@router.get("/sessions")
def sessions(request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> dict:
    return {"items": auth_service.list_sessions(db, user, get_session_token(request))}


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_session(session_id: int, request: Request, db: Session = Depends(get_db),
                   user: User = Depends(get_current_user)) -> Response:
    auth_service.revoke_session(db, user, session_id, get_session_token(request))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/sessions/revoke-others")
def revoke_others(request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> dict:
    return {"revoked": auth_service.revoke_other_sessions(db, user, get_session_token(request))}
