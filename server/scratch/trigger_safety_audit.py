import sys
sys.path.append("/home/laihome/projects/FoodScanIoT/server")
from module_c.safety_monitor import update_producer_safety_events

# Trigger safety events update for 統一企業 (ID 1)
print("Triggering safety audit for 統一企業 (ID 1)...")
update_producer_safety_events(1, "統一企業")
print("Done!")
