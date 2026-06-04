import hashlib
import secrets
import smtplib

from email.message import EmailMessage

from redis import Redis
from redis.exceptions import RedisError

from app.core.config import settings


CODE_EXPIRE_SECONDS = 180
SIGNUP_TOKEN_EXPIRE_SECONDS = 900
SEND_COOLDOWN_SECONDS = 60
MAX_VERIFY_ATTEMPTS = 5


# 26.06.04 김광원
# 이메일을 Redis key와 사용자 조회에 일관되게 사용할 수 있도록 정규화한다.
def normalize_email(email: str) -> str:
    return email.strip().lower()


# 26.06.04 김광원
# 인증 코드 저장 key를 생성한다.
def get_email_code_key(email: str) -> str:
    return f"auth:email_code:{email}"


# 26.06.04 김광원
# 인증 시도 횟수 저장 key를 생성한다.
def get_email_attempt_key(email: str) -> str:
    return f"auth:email_attempt:{email}"


# 26.06.04 김광원
# 인증 코드 재발송 제한 key를 생성한다.
def get_email_cooldown_key(email: str) -> str:
    return f"auth:email_cooldown:{email}"


# 26.06.04 김광원
# 회원가입 임시 토큰 저장 key를 생성한다.
def get_signup_token_key(signup_token: str) -> str:
    return f"auth:signup_token:{signup_token}"


# 26.06.04 김광원
# Redis에 저장할 민감 값 해시를 생성한다.
def hash_value(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


# 26.06.04 김광원
# 6자리 이메일 인증 코드를 생성한다.
def generate_verification_code() -> str:
    return f"{secrets.randbelow(1000000):06d}"


# 26.06.04 김광원
# 회원가입용 임시 토큰을 생성한다.
def generate_signup_token() -> str:
    return secrets.token_urlsafe(32)


# 26.06.04 김광원
# SMTP 설정으로 인증 메일을 발송한다.
def send_verification_email(email: str, code: str) -> None:
    if not settings.SMTP_HOST or not settings.SMTP_SENDER_EMAIL:
        raise RuntimeError("이메일 발송 설정이 없습니다.")

    message = EmailMessage()
    message["Subject"] = "[발신전용] PINLM 이메일 인증 코드"
    message["From"] = settings.SMTP_SENDER_EMAIL
    message["To"] = email
    message.set_content(
        "\n".join(
            [
                "PINLM 이메일 인증",
                "",
                "아래 인증 코드를 3분 안에 입력해 주세요.",
                "",
                f"인증 코드: {code}",
                "",
                "이 코드는 회원가입 인증에만 사용할 수 있습니다.",
                "본인이 요청하지 않았다면 이 메일을 무시해 주세요.",
            ]
        )
    )
    message.add_alternative(
        (
            "<html><body style=\"margin:0;padding:16px;background:#f8fafc;"
            "font-family:Arial,sans-serif;color:#111827;\">"
            "<div style=\"max-width:480px;margin:0 auto;background:#ffffff;"
            "border:1px solid #e5e7eb;border-radius:16px;overflow:hidden;\">"
            "<div style=\"padding:16px 20px;background:#111827;color:#ffffff;\">"
            "<div style=\"font-size:12px;letter-spacing:0.12em;opacity:0.85;\">PINLM</div>"
            "<div style=\"margin-top:6px;font-size:20px;font-weight:700;line-height:1.3;\">이메일 인증 코드</div>"
            "</div>"
            "<div style=\"padding:18px 20px 20px;\">"
            "<p style=\"margin:0 0 12px;font-size:14px;line-height:1.6;color:#374151;\">"
            "<strong style=\"color:#111827;\">3분 안에</strong> 아래 코드를 입력해 주세요."
            "</p>"
            "<div style=\"padding:18px 16px;background:#f8fafc;"
            "border:1px solid #dbe3ef;border-radius:12px;text-align:center;\">"
            "<div style=\"font-size:12px;color:#64748b;\">인증 코드</div>"
            f"<div style=\"margin-top:8px;font-size:30px;font-weight:700;letter-spacing:0.18em;color:#0f172a;\">{code}</div>"
            "</div>"
            "<p style=\"margin:12px 0 0;font-size:12px;line-height:1.6;color:#64748b;\">"
            "회원가입 인증용 코드입니다. 요청하지 않았다면 무시해 주세요."
            "</p>"
            "</div></div></body></html>"
        ),
        subtype="html",
    )

    with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT) as smtp:
        if settings.SMTP_USE_TLS:
            smtp.starttls()
        if settings.SMTP_USERNAME and settings.SMTP_PASSWORD:
            smtp.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
        smtp.send_message(message)


# 26.06.04 김광원
# 인증 코드를 발급하고 Redis에 저장한 뒤 메일로 발송한다.
def create_email_verification(redis: Redis, email: str) -> int:
    cooldown_key = get_email_cooldown_key(email)

    try:
        if redis.exists(cooldown_key):
            raise ValueError("인증 코드를 잠시 후 다시 요청해 주세요.")

        code = generate_verification_code()
        pipe = redis.pipeline()
        pipe.setex(get_email_code_key(email), CODE_EXPIRE_SECONDS, hash_value(code))
        pipe.setex(get_email_attempt_key(email), CODE_EXPIRE_SECONDS, "0")
        pipe.setex(cooldown_key, SEND_COOLDOWN_SECONDS, "1")
        pipe.execute()
    except RedisError:
        raise RuntimeError("인증 코드를 저장할 수 없습니다.")

    try:
        send_verification_email(email, code)
    except Exception:
        redis.delete(get_email_code_key(email), get_email_attempt_key(email), cooldown_key)
        raise RuntimeError("인증 메일 발송에 실패했습니다.")

    return CODE_EXPIRE_SECONDS


# 26.06.04 김광원
# 인증 코드를 검증하고 회원가입용 임시 토큰을 발급한다.
def verify_email_code(redis: Redis, email: str, code: str) -> str:
    code_key = get_email_code_key(email)
    attempt_key = get_email_attempt_key(email)

    try:
        saved_code = redis.get(code_key)
        if not saved_code:
            raise ValueError("인증 코드가 만료되었거나 존재하지 않습니다.")

        attempt_count = redis.incr(attempt_key)
        if attempt_count == 1:
            redis.expire(attempt_key, CODE_EXPIRE_SECONDS)
        if attempt_count > MAX_VERIFY_ATTEMPTS:
            redis.delete(code_key, attempt_key)
            raise ValueError("인증 시도 횟수를 초과했습니다.")

        if saved_code != hash_value(code):
            raise ValueError("인증 코드가 올바르지 않습니다.")

        signup_token = generate_signup_token()
        pipe = redis.pipeline()
        pipe.delete(code_key, attempt_key)
        pipe.setex(get_signup_token_key(signup_token), SIGNUP_TOKEN_EXPIRE_SECONDS, email)
        pipe.execute()
        return signup_token
    except RedisError:
        raise RuntimeError("이메일 인증을 처리할 수 없습니다.")


# 26.06.04 김광원
# 회원가입 임시 토큰이 해당 이메일에 대해 유효한지 확인한다.
def validate_signup_token(redis: Redis, email: str, signup_token: str) -> None:
    try:
        saved_email = redis.get(get_signup_token_key(signup_token))
    except RedisError:
        raise RuntimeError("회원가입 인증 정보를 확인할 수 없습니다.")

    if saved_email != email:
        raise ValueError("유효하지 않은 회원가입 인증 정보입니다.")


# 26.06.04 김광원
# 회원가입 완료 후 임시 토큰을 삭제한다.
def delete_signup_token(redis: Redis, signup_token: str) -> None:
    try:
        redis.delete(get_signup_token_key(signup_token))
    except RedisError:
        raise RuntimeError("회원가입 인증 정보를 정리할 수 없습니다.")
