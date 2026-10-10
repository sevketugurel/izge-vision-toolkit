from sqlalchemy.dialects.postgresql import insert
from . import schema as s

class DatabaseNotificationSink:
    """Transactional, in-app delivery; no external messages are sent."""
    def __init__(self, db, farm_id):
        self.db, self.farm_id = db, farm_id

    def notify(self, event_id: str, message: str) -> None:
        self.db.execute(insert(s.notifications).values(farm_id=self.farm_id, event_id=event_id, message=message)
                        .on_conflict_do_nothing(index_elements=['event_id']))
