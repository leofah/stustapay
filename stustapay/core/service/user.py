# pylint: disable=unexpected-keyword-arg
from typing import Optional

import asyncpg
from passlib.context import CryptContext
from pydantic import BaseModel

from stustapay.core.config import Config
from stustapay.core.schema.account import AccountType
from stustapay.core.schema.user import NewUser, Privilege, User, UserWithoutId
from stustapay.core.service.auth import AuthService, UserTokenMetadata
from stustapay.core.service.common.dbservice import DBService
from stustapay.core.service.common.decorators import requires_terminal, requires_user_privileges, with_db_transaction
from stustapay.core.service.common.error import NotFoundException


class UserLoginSuccess(BaseModel):
    user: User
    token: str


class UserService(DBService):
    def __init__(self, db_pool: asyncpg.Pool, config: Config, auth_service: AuthService):
        super().__init__(db_pool, config)
        self.auth_service = auth_service

        self.pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

    def _hash_password(self, password: str) -> str:
        return self.pwd_context.hash(password)

    def _check_password(self, password: str, hashed_password: str) -> bool:
        return self.pwd_context.verify(password, hashed_password)

    async def _create_user(
        self, *, conn: asyncpg.Connection, new_user: UserWithoutId, password: Optional[str] = None
    ) -> User:
        hashed_password = None
        if password:
            hashed_password = self._hash_password(password)

        user_id = await conn.fetchval(
            "insert into usr (name, description, password, user_tag_id, transport_account_id, cashier_account_id) "
            "values ($1, $2, $3, $4, $5, $6) returning id",
            new_user.name,
            new_user.description,
            hashed_password,
            new_user.user_tag_id,
            new_user.transport_account_id,
            new_user.cashier_account_id,
        )

        for privilege in new_user.privileges:
            await conn.execute("insert into usr_privs (usr, priv) values ($1, $2)", user_id, privilege.value)

        row = await conn.fetchrow("select * from usr_with_privileges where id = $1", user_id)
        return User.parse_obj(row)

    @with_db_transaction
    async def create_user_no_auth(
        self, *, conn: asyncpg.Connection, new_user: UserWithoutId, password: Optional[str] = None
    ) -> User:
        return await self._create_user(conn=conn, new_user=new_user, password=password)

    @with_db_transaction
    @requires_user_privileges([Privilege.admin])
    async def create_user(
        self, *, conn: asyncpg.Connection, new_user: UserWithoutId, password: Optional[str] = None
    ) -> User:
        return await self._create_user(conn=conn, new_user=new_user, password=password)

    @with_db_transaction
    @requires_terminal([Privilege.admin])
    async def create_cashier(self, *, conn: asyncpg.Connection, current_user: User, new_user: NewUser) -> User:
        user = await self.create_user_with_tag(current_user=current_user, conn=conn, new_user=new_user)
        user = await self.promote_to_cashier(current_user=current_user, conn=conn, user_id=user.id)
        return user

    @with_db_transaction
    @requires_terminal([Privilege.admin])
    async def create_finanzorga(self, *, conn: asyncpg.Connection, current_user: User, new_user: NewUser) -> User:
        user = await self.create_user_with_tag(current_user=current_user, conn=conn, new_user=new_user)
        user = await self.promote_to_cashier(current_user=current_user, conn=conn, user_id=user.id)
        user = await self.promote_to_finanzorga(current_user=current_user, conn=conn, user_id=user.id)
        return user

    @with_db_transaction
    @requires_user_privileges([Privilege.admin])
    async def create_user_with_tag(self, *, conn: asyncpg.Connection, new_user: NewUser) -> User:
        """
        Create a user at a Terminal, where a name and the user tag must be provided
        If a user with the given tag already exists, this user is returned, without updating the name

        returns the created user
        """
        user_tag_id = await conn.fetchval("select id from user_tag where uid = $1", new_user.user_tag)
        if user_tag_id is None:
            raise NotFoundException(element_typ="user_tag", element_id=str(new_user.user_tag))

        existing_user = await conn.fetchrow("select * from usr_with_privileges where user_tag_id = $1", user_tag_id)
        if existing_user is not None:
            # ignore the name provided in new_user
            return User.parse_obj(existing_user)

        user = UserWithoutId(
            name=new_user.name,
            privileges=[],
            user_tag_id=user_tag_id,
        )
        return await self._create_user(conn=conn, new_user=user)

    @with_db_transaction
    @requires_user_privileges([Privilege.admin])
    async def promote_to_cashier(self, *, conn: asyncpg.Connection, user_id: int) -> User:
        user = await self._get_user(conn=conn, user_id=user_id)
        if user is None:
            raise NotFoundException(element_typ="user", element_id=str(user_id))

        if Privilege.cashier in user.privileges:
            return user

        # create cashier account
        user.cashier_account_id = await conn.fetchval(
            "insert into account (type, name) values ($1, $2) returning id",
            AccountType.internal.value,
            f"Cashier account for {user.name}",
        )
        user.privileges.append(Privilege.cashier)
        return await self._update_user(conn=conn, user_id=user.id, user=user)

    @with_db_transaction
    @requires_user_privileges([Privilege.admin])
    async def promote_to_finanzorga(self, *, conn: asyncpg.Connection, user_id: int) -> User:
        user = await self._get_user(conn=conn, user_id=user_id)
        if user is None:
            raise NotFoundException(element_typ="user", element_id=str(user_id))

        if Privilege.finanzorga in user.privileges:
            return user

        # create backpack account
        user.transport_account_id = await conn.fetchval(
            "insert into account (type, name) values ($1, $2) returning id",
            AccountType.internal.value,
            f"Transport account for finanzorga {user.name}",
        )
        user.privileges.append(Privilege.finanzorga)
        return await self._update_user(conn=conn, user_id=user.id, user=user)

    @with_db_transaction
    @requires_user_privileges([Privilege.admin])
    async def list_users(self, *, conn: asyncpg.Connection) -> list[User]:
        cursor = conn.cursor("select * from usr_with_privileges")
        result = []
        async for row in cursor:
            result.append(User.parse_obj(row))
        return result

    async def _get_user(self, conn: asyncpg.Connection, user_id: int) -> User:
        row = await conn.fetchrow("select * from usr_with_privileges where id = $1", user_id)
        if row is None:
            raise NotFoundException(element_typ="user", element_id=str(user_id))
        return User.parse_obj(row)

    @with_db_transaction
    @requires_user_privileges([Privilege.admin])
    async def get_user(self, *, conn: asyncpg.Connection, user_id: int) -> Optional[User]:
        return await self._get_user(conn, user_id)

    async def _update_user(self, *, conn: asyncpg.Connection, user_id: int, user: UserWithoutId) -> User:
        row = await conn.fetchrow(
            "update usr "
            "set name = $2, description = $3, user_tag_id = $4, transport_account_id = $5, cashier_account_id = $6 "
            "where id = $1 returning id",
            user_id,
            user.name,
            user.description,
            user.user_tag_id,
            user.transport_account_id,
            user.cashier_account_id,
        )
        if row is None:
            raise NotFoundException(element_typ="user", element_id=str(user_id))

        # Update privileges
        await conn.execute("delete from usr_privs where usr = $1", user_id)
        for privilege in user.privileges:
            await conn.execute("insert into usr_privs (usr, priv) values ($1, $2)", user_id, privilege.value)

        return await self._get_user(conn, user_id)

    @with_db_transaction
    @requires_user_privileges([Privilege.admin])
    async def update_user(self, *, conn: asyncpg.Connection, user_id: int, user: UserWithoutId) -> Optional[User]:
        return await self._update_user(conn=conn, user_id=user_id, user=user)

    @with_db_transaction
    @requires_user_privileges([Privilege.admin])
    async def delete_user(self, *, conn: asyncpg.Connection, user_id: int) -> bool:
        result = await conn.execute(
            "delete from usr where id = $1",
            user_id,
        )
        return result != "DELETE 0"

    @with_db_transaction
    @requires_user_privileges([Privilege.admin])
    async def link_user_to_cashier_account(self, *, conn: asyncpg.Connection, user_id: int, account_id: int) -> bool:
        # TODO: FIXME: is this the way it's going to stay?
        result = await conn.fetchval(
            "update usr set cashier_account_id = $2 where id = $1 returning id",
            user_id,
            account_id,
        )
        return result is not None

    @with_db_transaction
    @requires_user_privileges([Privilege.admin])
    async def link_user_to_transport_account(self, *, conn: asyncpg.Connection, user_id: int, account_id: int) -> bool:
        # TODO: FIXME: is this the way it's going to stay?
        result = await conn.fetchval(
            "update usr set transport_account_id = $2 where id = $1 returning id",
            user_id,
            account_id,
        )
        return result is not None

    @with_db_transaction
    async def login_user(self, *, conn: asyncpg.Connection, username: str, password: str) -> Optional[UserLoginSuccess]:
        row = await conn.fetchrow(
            "select * from usr_with_privileges where name = $1",
            username,
        )
        if row is None:
            return None

        if not self._check_password(password, row["password"]):
            return None

        user = User.parse_obj(row)

        session_id = await conn.fetchval("insert into usr_session (usr) values ($1) returning id", user.id)
        token = self.auth_service.create_user_access_token(UserTokenMetadata(user_id=user.id, session_id=session_id))
        return UserLoginSuccess(
            user=user,
            token=token,
        )

    @with_db_transaction
    @requires_user_privileges()
    async def logout_user(self, *, conn: asyncpg.Connection, current_user: User, token: str) -> bool:
        token_payload = self.auth_service.decode_user_jwt_payload(token)
        if token_payload is None:
            return False

        if current_user.id != token_payload.user_id:
            return False

        result = await conn.execute(
            "delete from usr_session where usr = $1 and id = $2", current_user.id, token_payload.session_id
        )
        return result != "DELETE 0"
