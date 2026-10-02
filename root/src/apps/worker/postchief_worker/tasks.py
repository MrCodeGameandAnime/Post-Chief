from postchief.config import Settings
from postchief_worker.application import create_worker

app = create_worker(Settings())
