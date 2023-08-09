import threading


# Lock for use when reading from TinyDB.
#
# This is a somewhat dubious workaround for TinyDB issues. The DB sometimes gets
# into a weird state where more than one instance of the DB's JSON data is
# written to the db file on disk. This seems to be related to read/write races.
# (TinyDB is itself not concurrency-aware).
#
# This relies on all the DB-read-related endpoint functions in FastAPI (which
# call these manager methods) being "def" not "async def". FastAPI runs "def"
# handlers in threads not coroutines; so this DB race workaround uses a
# threading.Lock() to prevent multiple reads at once.
#
# Also, the "tinyrecord" package is already used for atomic writes to TinyDB.
# This mix of "tinyrecord" for writes and a thread lock for reads seems dubious,
# although it's an improvement so it'll do for now. Ultimately the persistence
# solution should be reconsidered entirely.
DB_READ_LOCK = threading.Lock()
