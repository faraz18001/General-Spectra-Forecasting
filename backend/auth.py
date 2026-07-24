from datetime import datetime, timedelta
from jose import JWTError, jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from database import login, signup, get_user_by_id
import os

# JWT Configuration
SECRET_KEY = os.getenv("JWT_SECRET_KEY", "your-secret-key-change-in-production")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = 24

# Security scheme
security = HTTPBearer()


# --- Pydantic Models for Auth ---
class SignupRequest(BaseModel):
    email: str
    name: str
    password: str


class LoginRequest(BaseModel):
    email: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str
    user: dict


class UserResponse(BaseModel):
    id: int
    email: str
    name: str
    role: str


# --- JWT Functions ---
def create_access_token(user_id, email, role, allowed_branches=None):
    """
    Create a secure JWT access token for authentication.
    
    Generates a token signed with the system's secret key and containing specific claims 
    representing user ID, email, role, and authorized branches. Includes an expiration window.
    
    Args:
        user_id (int): Primary key ID of the user.
        email (str): Registered email of the user.
        role (str): Authorized role (e.g. 'admin' or 'user').
        allowed_branches (str, optional): Comma-separated branch name whitelist or None for unrestricted. Defaults to None.
        
    Returns:
        str: Encoded JWT access token.
    """
    expire = datetime.utcnow() + timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS)

    payload = {"sub": str(user_id), "email": email, "role": role, "allowed_branches": allowed_branches or "", "exp": expire}

    token = jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)
    return token


def verify_token(token):
    """
    Verify, validate, and decode an encoded JWT token.
    
    Checks expiration claims and decodes the payload parameters using the system's secret key.
    
    Args:
        token (str): Encoded JWT string.
        
    Returns:
        dict or None: Decoded JWT claims dictionary if validation succeeds, else None.
    """
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = payload.get("sub")
        if user_id is None:
            return None
        return payload
    except JWTError:
        return None


# --- Auth Dependencies ---
def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """
    FastAPI dependency to retrieve the currently logged in user based on the authorization header.
    
    Extracts the JWT bearer token, decodes it, verifies the expiration, checks if the user exists
    in the database, and builds a payload dictionary.
    
    Args:
        credentials (HTTPAuthorizationCredentials): Bearer token from authorization header.
        
    Returns:
        dict: Currently authenticated user information containing:
            - id (int): Database ID of the user.
            - email (str): Email address of the user.
            - name (str): Full name of the user.
            - role (str): Role assigned to the user.
            - allowed_branches (str): Comma separated authorized branch names.
            
    Raises:
        HTTPException: 401 Unauthorized if the token is invalid, expired, or user is not found.
    """
    token = credentials.credentials
    payload = verify_token(token)

    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_id = int(payload.get("sub"))
    user = get_user_by_id(user_id)

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )

    return {"id": user.id, "email": user.email, "name": user.name, "role": user.role, "allowed_branches": user.allowed_branches or ""}


def require_admin(current_user: dict = Depends(get_current_user)):
    """
    FastAPI security dependency enforcing administrative privilege checks on endpoints.
    
    Requires that the active authenticated user has the 'admin' role.
    
    Args:
        current_user (dict): Decoded user dictionary retrieved via Depends(get_current_user).
        
    Returns:
        dict: The verified user info dictionary.
        
    Raises:
        HTTPException: 403 Forbidden if the user's role is not 'admin'.
    """
    if current_user.get("role") != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required"
        )
    return current_user


# --- Auth Endpoint Functions ---
def handle_signup(request: SignupRequest):
    """
    Orchestrates the signup operations: registers user, hashes passwords, and returns access token.
    
    Args:
        request (SignupRequest): Pydantic validation schema containing email, name, and cleartext password.
        
    Returns:
        TokenResponse: Pydantic response containing the access_token, bearer type, and user dictionary.
        
    Raises:
        HTTPException: 400 Bad Request if signup fails due to duplicate email or database issues.
    """
    user, error = signup(
        email=request.email,
        name=request.name,
        password=request.password,
        role="user",  # Default role
    )

    if error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=error)

    # Create token for the new user
    token = create_access_token(user.id, user.email, user.role, user.allowed_branches)

    return TokenResponse(
        access_token=token,
        token_type="bearer",
        user={"id": user.id, "email": user.email, "name": user.name, "role": user.role, "allowed_branches": user.allowed_branches or ""},
    )


def handle_login(request: LoginRequest):
    """
    Orchestrates user login operations: verifies password, generates JWT token, and returns response.
    
    Args:
        request (LoginRequest): Pydantic validation schema containing user email and plain password.
        
    Returns:
        TokenResponse: Pydantic response containing the access_token, bearer type, and user profile data.
        
    Raises:
        HTTPException: 401 Unauthorized if authentication fails (invalid credentials or deactivated user).
    """
    user, error = login(request.email, request.password)

    if error:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=error)

    # Create token
    token = create_access_token(user.id, user.email, user.role, user.allowed_branches)

    return TokenResponse(
        access_token=token,
        token_type="bearer",
        user={"id": user.id, "email": user.email, "name": user.name, "role": user.role, "allowed_branches": user.allowed_branches or ""},
    )
