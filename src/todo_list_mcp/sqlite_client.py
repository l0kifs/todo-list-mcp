"""
SQLite Client.

This module provides a general-purpose client for interacting with SQLite databases
using SQLAlchemy ORM. It supports session management, transaction handling,
and common database operations with ORM models.
"""

from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional, Sequence, Type, TypeVar

from loguru import logger
from sqlalchemy import Engine, create_engine, inspect, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool

# Type variable for ORM models
T = TypeVar("T", bound=DeclarativeBase)


class SQLiteClientError(Exception):
    """Base exception for SQLite client errors."""

    pass


class SQLiteConnectionError(SQLiteClientError):
    """Raised when database connection fails."""

    pass


class SQLiteQueryError(SQLiteClientError):
    """Raised when query execution fails."""

    pass


class SQLiteTransactionError(SQLiteClientError):
    """Raised when transaction management fails."""

    pass


class SQLiteClient:
    """
    General-purpose SQLite client using SQLAlchemy ORM.

    Provides session management, transaction handling, and common database operations
    with ORM models and automatic resource cleanup.

    Args:
        database_url: SQLite database URL (e.g., "sqlite:///path/to/db.sqlite")
        connect_args: Additional connection arguments (default: check_same_thread=False)
        pool_class: SQLAlchemy pool class (default: StaticPool for SQLite)
        echo: Enable SQL query logging (default: False)

    Example:
        >>> from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
        >>> class Base(DeclarativeBase): pass
        >>> class User(Base):
        ...     __tablename__ = "users"
        ...     id: Mapped[int] = mapped_column(primary_key=True)
        ...     name: Mapped[str]
        >>> client = SQLiteClient("sqlite:///data/app.db")
        >>> client.create_tables(Base)
        >>> with client.session() as session:
        ...     user = User(name="Alice")
        ...     client.add(session, user)
    """

    def __init__(
        self,
        database_url: str,
        connect_args: Optional[Dict[str, Any]] = None,
        pool_class: Optional[type] = StaticPool,
        echo: bool = False,
    ):
        """Initialize SQLite client with connection parameters."""
        self.database_url = database_url
        self._connect_args = connect_args or {"check_same_thread": False}
        self._pool_class = pool_class
        self._echo = echo
        self._engine: Optional[Engine] = None
        self._session_factory: Optional[sessionmaker] = None

        logger.debug(
            "Initializing SQLite client",
            database_url=database_url,
            pool_class=pool_class.__name__ if pool_class else None,
        )

    @property
    def engine(self) -> Engine:
        """Get or create SQLAlchemy engine with lazy initialization."""
        if self._engine is None:
            try:
                logger.debug("Creating SQLAlchemy engine")
                self._engine = create_engine(
                    self.database_url,
                    connect_args=self._connect_args,
                    poolclass=self._pool_class,
                    echo=self._echo,
                )
                logger.info("SQLite engine created successfully", url=self.database_url)
            except SQLAlchemyError as e:
                logger.error("Failed to create SQLite engine", error=str(e))
                raise SQLiteConnectionError(f"Failed to create engine: {e}") from e
        return self._engine

    @property
    def session_factory(self) -> sessionmaker:
        """Get or create session factory with lazy initialization."""
        if self._session_factory is None:
            self._session_factory = sessionmaker(
                bind=self.engine, expire_on_commit=False
            )
            logger.debug("Session factory created")
        return self._session_factory

    def ensure_database_exists(self) -> None:
        """
        Ensure the database file exists, creating parent directories if needed.

        Raises:
            SQLiteConnectionError: If database directory cannot be created
        """
        if self.database_url.startswith("sqlite:///"):
            db_path = Path(self.database_url.replace("sqlite:///", ""))
            try:
                db_path.parent.mkdir(parents=True, exist_ok=True)
                logger.debug("Database directory ensured", path=str(db_path.parent))
            except OSError as e:
                logger.error("Failed to create database directory", error=str(e))
                raise SQLiteConnectionError(
                    f"Failed to create database directory: {e}"
                ) from e

    @contextmanager
    def session(self) -> Generator[Session, None, None]:
        """
        Context manager for database sessions with automatic cleanup.

        Yields:
            Session: SQLAlchemy ORM session object

        Raises:
            SQLiteConnectionError: If session creation fails

        Example:
            >>> with client.session() as session:
            ...     users = client.get_all(session, User)
        """
        session = None
        try:
            logger.debug("Creating database session")
            session = self.session_factory()
            logger.debug("Database session created")
            yield session
        except SQLAlchemyError as e:
            logger.error("Session creation failed", error=str(e))
            raise SQLiteConnectionError(f"Session creation failed: {e}") from e
        finally:
            if session is not None:
                logger.debug("Closing database session")
                session.close()

    @contextmanager
    def transaction(self) -> Generator[Session, None, None]:
        """
        Context manager for database transactions with automatic commit/rollback.

        Automatically commits on successful execution and rolls back on exceptions.

        Yields:
            Session: SQLAlchemy ORM session object within transaction

        Raises:
            SQLiteTransactionError: If transaction fails

        Example:
            >>> with client.transaction() as session:
            ...     user = User(name="Alice")
            ...     client.add(session, user)
        """
        with self.session() as session:
            try:
                logger.debug("Transaction started")
                yield session
                session.commit()
                logger.debug("Transaction committed")
            except Exception as e:
                logger.error("Transaction failed, rolling back", error=str(e))
                session.rollback()
                raise SQLiteTransactionError(f"Transaction failed: {e}") from e

    def create_tables(self, base: Type[DeclarativeBase]) -> None:
        """
        Create all tables defined in the declarative base.

        Args:
            base: SQLAlchemy DeclarativeBase class with table definitions

        Raises:
            SQLiteConnectionError: If table creation fails

        Example:
            >>> from sqlalchemy.orm import DeclarativeBase
            >>> class Base(DeclarativeBase): pass
            >>> client.create_tables(Base)
        """
        try:
            logger.info("Creating database tables")
            base.metadata.create_all(self.engine)
            logger.info("Database tables created successfully")
        except SQLAlchemyError as e:
            logger.error("Table creation failed", error=str(e))
            raise SQLiteConnectionError(f"Table creation failed: {e}") from e

    def drop_tables(self, base: Type[DeclarativeBase]) -> None:
        """
        Drop all tables defined in the declarative base.

        Args:
            base: SQLAlchemy DeclarativeBase class with table definitions

        Raises:
            SQLiteConnectionError: If table dropping fails

        Example:
            >>> client.drop_tables(Base)
        """
        try:
            logger.warning("Dropping database tables")
            base.metadata.drop_all(self.engine)
            logger.info("Database tables dropped successfully")
        except SQLAlchemyError as e:
            logger.error("Table dropping failed", error=str(e))
            raise SQLiteConnectionError(f"Table dropping failed: {e}") from e

    def add(self, session: Session, instance: T) -> T:
        """
        Add a model instance to the session.

        Args:
            session: Active database session
            instance: ORM model instance to add

        Returns:
            The added instance

        Raises:
            SQLiteQueryError: If add operation fails

        Example:
            >>> with client.transaction() as session:
            ...     user = User(name="Alice")
            ...     client.add(session, user)
        """
        try:
            logger.debug("Adding instance to session", model=type(instance).__name__)
            session.add(instance)
            logger.debug("Instance added successfully")
            return instance
        except SQLAlchemyError as e:
            logger.error("Add operation failed", error=str(e))
            raise SQLiteQueryError(f"Add operation failed: {e}") from e

    def add_all(self, session: Session, instances: Sequence[T]) -> Sequence[T]:
        """
        Add multiple model instances to the session.

        Args:
            session: Active database session
            instances: List of ORM model instances to add

        Returns:
            The added instances

        Raises:
            SQLiteQueryError: If add operation fails

        Example:
            >>> with client.transaction() as session:
            ...     users = [User(name="Alice"), User(name="Bob")]
            ...     client.add_all(session, users)
        """
        try:
            logger.debug("Adding multiple instances to session", count=len(instances))
            session.add_all(instances)
            logger.debug("Instances added successfully")
            return instances
        except SQLAlchemyError as e:
            logger.error("Batch add operation failed", error=str(e))
            raise SQLiteQueryError(f"Batch add operation failed: {e}") from e

    def get_by_id(self, session: Session, model: Type[T], id_value: Any) -> Optional[T]:
        """
        Get a model instance by its primary key.

        Args:
            session: Active database session
            model: ORM model class
            id_value: Primary key value

        Returns:
            Model instance or None if not found

        Raises:
            SQLiteQueryError: If query fails

        Example:
            >>> with client.session() as session:
            ...     user = client.get_by_id(session, User, 1)
            ...     if user:
            ...         print(user.name)
        """
        try:
            logger.debug("Fetching by ID", model=model.__name__, id=id_value)
            instance = session.get(model, id_value)
            logger.debug("Fetch by ID completed", found=instance is not None)
            return instance
        except SQLAlchemyError as e:
            logger.error("Get by ID failed", error=str(e))
            raise SQLiteQueryError(f"Get by ID failed: {e}") from e

    def query(self, session: Session, model: Type[T]) -> Any:
        """
        Create a query for a model.

        Args:
            session: Active database session
            model: ORM model class

        Returns:
            SQLAlchemy select statement that can be further filtered

        Example:
            >>> with client.session() as session:
            ...     stmt = client.query(session, User).where(User.name == "Alice")
            ...     users = session.scalars(stmt).all()
        """
        logger.debug("Creating query", model=model.__name__)
        return select(model)

    def get_all(self, session: Session, model: Type[T]) -> Sequence[T]:
        """
        Get all instances of a model.

        Args:
            session: Active database session
            model: ORM model class

        Returns:
            List of all model instances

        Raises:
            SQLiteQueryError: If query fails

        Example:
            >>> with client.session() as session:
            ...     users = client.get_all(session, User)
            ...     for user in users:
            ...         print(user.name)
        """
        try:
            logger.debug("Fetching all instances", model=model.__name__)
            stmt = select(model)
            result = session.scalars(stmt).all()
            logger.debug("Fetch all completed", count=len(result))
            return result
        except SQLAlchemyError as e:
            logger.error("Get all failed", error=str(e))
            raise SQLiteQueryError(f"Get all failed: {e}") from e

    def delete(self, session: Session, instance: T) -> None:
        """
        Delete a model instance from the database.

        Args:
            session: Active database session
            instance: ORM model instance to delete

        Raises:
            SQLiteQueryError: If delete operation fails

        Example:
            >>> with client.transaction() as session:
            ...     user = client.get_by_id(session, User, 1)
            ...     if user:
            ...         client.delete(session, user)
        """
        try:
            logger.debug("Deleting instance", model=type(instance).__name__)
            session.delete(instance)
            logger.debug("Instance deleted successfully")
        except SQLAlchemyError as e:
            logger.error("Delete operation failed", error=str(e))
            raise SQLiteQueryError(f"Delete operation failed: {e}") from e

    def table_exists(self, table_name: str) -> bool:
        """
        Check if a table exists in the database.

        Args:
            table_name: Name of the table to check

        Returns:
            True if table exists, False otherwise

        Example:
            >>> if client.table_exists("users"):
            ...     print("Users table exists")
        """
        inspector = inspect(self.engine)
        exists = table_name in inspector.get_table_names()
        logger.debug("Table existence check", table=table_name, exists=exists)
        return exists

    def get_tables(self) -> List[str]:
        """
        Get list of all tables in the database.

        Returns:
            List of table names

        Example:
            >>> tables = client.get_tables()
            >>> print(f"Found {len(tables)} tables")
        """
        inspector = inspect(self.engine)
        tables = inspector.get_table_names()
        logger.debug("Retrieved table list", count=len(tables))
        return tables

    def vacuum(self) -> None:
        """
        Run VACUUM command to reclaim unused space and optimize database.

        Example:
            >>> client.vacuum()
        """
        try:
            logger.info("Running VACUUM command")
            with self.engine.connect() as conn:
                conn.exec_driver_sql("VACUUM")
                conn.commit()
            logger.info("VACUUM completed successfully")
        except SQLAlchemyError as e:
            logger.error("VACUUM failed", error=str(e))
            raise SQLiteQueryError(f"VACUUM failed: {e}") from e

    def close(self) -> None:
        """
        Close the database engine and cleanup resources.

        Should be called when client is no longer needed.

        Example:
            >>> client = SQLiteClient("sqlite:///data/app.db")
            >>> try:
            ...     # Use client
            ...     pass
            ... finally:
            ...     client.close()
        """
        if self._engine is not None:
            logger.info("Closing SQLite engine")
            self._engine.dispose()
            self._engine = None
            logger.info("SQLite engine closed successfully")

    def __enter__(self):
        """Support using client as context manager."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Cleanup resources when exiting context."""
        self.close()
        return False


if __name__ == "__main__":
    # Example usage
    import sys
    from datetime import datetime, timezone

    from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

    # Configure logger to stderr
    logger.remove()
    logger.add(sys.stderr, level="INFO")

    # Define ORM models
    class Base(DeclarativeBase):
        pass

    class User(Base):
        __tablename__ = "users"

        id: Mapped[int] = mapped_column(primary_key=True)
        name: Mapped[str]
        email: Mapped[str]
        created_at: Mapped[datetime] = mapped_column(
            default=lambda: datetime.now(timezone.utc)
        )

        def __repr__(self) -> str:
            return f"User(id={self.id}, name={self.name!r}, email={self.email!r})"

    try:
        # Create a test database
        db_path = "/tmp/example_crypto.db"
        client = SQLiteClient(f"sqlite:///{db_path}")

        logger.info("Example 1: Creating tables and inserting data")
        client.create_tables(Base)

        with client.transaction() as session:
            # Create some users
            users = [
                User(name="Alice", email="alice@example.com"),
                User(name="Bob", email="bob@example.com"),
                User(name="Charlie", email="charlie@example.com"),
            ]
            client.add_all(session, users)
            logger.info(f"Inserted {len(users)} users successfully")

        logger.info("\nExample 2: Querying data with ORM")
        with client.session() as session:
            # Get all users
            all_users = client.get_all(session, User)
            logger.info(f"Retrieved {len(all_users)} users:")
            for user in all_users:
                logger.info(f"  {user}")

            # Get user by ID
            user = client.get_by_id(session, User, 2)
            if user:
                logger.info(f"\nFound user by ID: {user}")

            # Custom query with filter
            stmt = client.query(session, User).where(User.name == "Alice")
            alice = session.scalars(stmt).first()
            if alice:
                logger.info(f"Found user by name: {alice}")

        logger.info("\nExample 3: Updating data")
        with client.transaction() as session:
            user = client.get_by_id(session, User, 1)
            if user:
                user.email = "alice.updated@example.com"
                logger.info(f"Updated user email: {user}")

        logger.info("\nExample 4: Checking table existence and metadata")
        if client.table_exists("users"):
            logger.info("Table 'users' exists")

        tables = client.get_tables()
        logger.info(f"All tables in database: {tables}")

        logger.info("\nExample 5: Using context manager")
        with SQLiteClient(f"sqlite:///{db_path}") as ctx_client:
            with ctx_client.session() as session:
                count = len(ctx_client.get_all(session, User))
                logger.info(f"Total users in database: {count}")

        logger.info("\nAll examples completed successfully!")

    except Exception as e:
        logger.error(f"An error occurred: {e}")