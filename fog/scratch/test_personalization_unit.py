import sys
import copy
sys.path.append("/home/johnlai/projects/fog")
from main import calculate_personalized_score

# Mock result from Cloud
mock_result = {
    "health_score": 75,
    "risk_level": "medium",
    "grade": "C",
    "final_health_diagnosis": {
        "score": 75,
        "grade": "C",
        "warnings": []
    },
    "ingredients_detail": [],
    "nutrition_facts": {
        "calories": 200,
        "protein": 5,
        "fat": 15,
        "sugar": 18,   # > 10 (triggers diabetes warning)
        "sodium": 500  # > 400 (triggers hypertension warning)
    }
}

# 1. Test old format (group: hypertension)
user_conditions_old_hyper = {
    "group": "hypertension",
    "allergens": []
}

res_old_hyper = calculate_personalized_score(copy.deepcopy(mock_result), user_conditions_old_hyper)
print("Old Format (hypertension) Warnings:")
print(res_old_hyper.get("final_health_diagnosis", {}).get("warnings"))

# 2. Test old format (group: diabetes)
user_conditions_old_diab = {
    "group": "diabetes",
    "allergens": []
}

res_old_diab = calculate_personalized_score(copy.deepcopy(mock_result), user_conditions_old_diab)
print("\nOld Format (diabetes) Warnings:")
print(res_old_diab.get("final_health_diagnosis", {}).get("warnings"))

# 3. Test new format (group: adult, chronic_conditions: [hypertension, diabetes])
user_conditions_new = {
    "group": "adult",
    "allergens": [],
    "chronic_conditions": ["hypertension", "diabetes"]
}

res_new = calculate_personalized_score(copy.deepcopy(mock_result), user_conditions_new)
print("\nNew Format (adult + chronic_conditions) Warnings:")
print(res_new.get("final_health_diagnosis", {}).get("warnings"))
