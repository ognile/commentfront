import json
import os
import logging

class MappingManager:
    def __init__(self, file_path="mappings.json"):
        self.file_path = file_path
        self.mappings = {} # ProfileID -> UID
        self.load()

    def load(self):
        if os.path.exists(self.file_path):
            try:
                with open(self.file_path, "r") as f:
                    self.mappings = json.load(f)
            except:
                self.mappings = {}
        else:
            self.mappings = {}

    def save(self):
        with open(self.file_path, "w") as f:
            json.dump(self.mappings, f, indent=2)

    def get_uid(self, profile_id):
        return self.mappings.get(profile_id)

    def set_mapping(self, profile_id, uid):
        self.mappings[profile_id] = uid
        self.save()

    def delete_mapping(self, profile_id):
        if profile_id in self.mappings:
            del self.mappings[profile_id]
            self.save()
