"""Automatic publication of private inventory is disabled."""
def publish_to_repo():
    return {"status":"disabled", "reason":"Inventory is local; publish source only"}
