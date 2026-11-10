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


@router.post(
    "/login", response_model=LoginResponse, dependencies=[Depends(enforce_login_rate_limit)]
)
def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> LoginResponse:
    issued = auth_service.login(
        db, settings, body.email, body.password,
        ip=client_ip(request), user_agent=request.headers.get("user-agent"),
    )
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
