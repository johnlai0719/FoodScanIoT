import math

class NutriScoreV7:
    """
    Nutri-Score 2023 (V7) Deterministic Calculator
    Based on the official FAQ V7 (Dec 2023)
    """

    def __init__(self):
        # General Food N-points thresholds (Upper bounds for each point)
        self.energy_thresholds = [335 * i for i in range(1, 11)]  # 335, 670, ... 3350
        self.sugar_thresholds = [3.4, 6.8, 10, 14, 17, 20, 24, 27, 31, 34, 37, 41, 44, 48, 51]
        self.sfa_thresholds = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
        self.salt_thresholds = [0.2, 0.4, 0.6, 0.8, 1.0, 1.2, 1.4, 1.6, 1.8, 2.0, 2.2, 2.4, 2.6, 2.8, 3.0, 3.2, 3.4, 3.6, 3.8, 4.0]

        # General Food P-points thresholds
        self.protein_thresholds = [2.4, 4.8, 7.2, 9.6, 12, 14, 17]
        self.fibre_thresholds = [3.0, 4.1, 5.2, 6.3, 7.4]
        self.fruit_veg_thresholds = [40, 60, 80]

        # Beverage specific thresholds (Updated V7 rules)
        self.beverage_energy_thresholds = [30, 90, 150, 210, 240, 270, 300, 330, 360, 390]
        self.beverage_sugar_thresholds = [0.5, 2.0, 3.5, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0, 11.0]
        self.beverage_protein_thresholds = [1.2, 1.5, 1.8, 2.1, 2.4, 2.7, 3.0]
        self.beverage_fibre_thresholds = [3.0, 4.1, 5.2, 6.3, 7.4, 8.0]

    def _get_points(self, value, thresholds):
        points = 0
        for t in thresholds:
            if value > t:
                points += 1
            else:
                break
        return points

    def calculate(self, data, is_beverage=False, is_cheese=False, is_red_meat=False, has_sweeteners=False, is_water=False):
        """
        data: { energy, sugars, sfa, salt, proteins, fibres, fruit_veg_pct }
        salt is in grams. energy in kJ.
        """
        # 1. Calculate N components (Negative)
        if is_beverage:
            n_energy = self._get_points(data.get('energy', 0), self.beverage_energy_thresholds)
            n_sugars = self._get_points(data.get('sugars', 0), self.beverage_sugar_thresholds)
        else:
            n_energy = self._get_points(data.get('energy', 0), self.energy_thresholds)
            n_sugars = self._get_points(data.get('sugars', 0), self.sugar_thresholds)
            
        n_sfa = self._get_points(data.get('sfa', 0), self.sfa_thresholds)
        n_salt = self._get_points(data.get('salt', 0), self.salt_thresholds)
        
        points_n = n_energy + n_sugars + n_sfa + n_salt

        # Non-nutritive sweetener penalty for beverages (V7 rule: +4 points for presence)
        n_sweeteners = 0
        if is_beverage and has_sweeteners:
            n_sweeteners = 4
            points_n += n_sweeteners
        
        # 2. Calculate P components (Positive)
        # [Local Adjustment] In Taiwan, if fibres or fruit_veg_pct are not explicitly 
        # labeled, they MUST be treated as 0 for conservative scoring.
        if is_beverage:
            p_protein = self._get_points(data.get('proteins', 0), self.beverage_protein_thresholds)
            
            fibres_val = data.get('fibres')
            p_fibre = self._get_points(fibres_val, self.beverage_fibre_thresholds) if fibres_val is not None else 0
            
            p_fruit_veg = 0
            fv_pct = data.get('fruit_veg_pct')
            if fv_pct is not None:
                if fv_pct > 80: p_fruit_veg = 6
                elif fv_pct > 60: p_fruit_veg = 4
                elif fv_pct > 40: p_fruit_veg = 2
        else:
            p_protein = self._get_points(data.get('proteins', 0), self.protein_thresholds)
            
            fibres_val = data.get('fibres')
            p_fibre = self._get_points(fibres_val, self.fibre_thresholds) if fibres_val is not None else 0
            
            p_fruit_veg = 0
            fv_pct = data.get('fruit_veg_pct')
            if fv_pct is not None:
                if fv_pct > 80: p_fruit_veg = 5
                elif fv_pct > 60: p_fruit_veg = 2
                elif fv_pct > 40: p_fruit_veg = 1
        
        # Red meat exception: max protein points = 2
        if is_red_meat:
            p_protein = min(p_protein, 2)
            
        points_p = p_protein + p_fibre + p_fruit_veg

        # 3. Final Score logic (Decision Tree)
        if is_beverage:
            # Beverage protein cap is removed under V7 rules
            score = points_n - points_p
        elif points_n < 11 or is_cheese:
            score = points_n - points_p
        else:
            # Special case for N >= 11 for solid foods
            if p_fruit_veg == 5:
                score = points_n - points_p
            else:
                score = points_n - (p_fibre + p_fruit_veg)

        # 4. Grade Attribution
        grade = self.get_grade(score, is_beverage, is_water)
        
        return {
            "score": score,
            "grade": grade,
            "details": {
                "points_n": points_n,
                "points_p": points_p,
                "breakdown": {
                    "energy": n_energy, "sugars": n_sugars, "sfa": n_sfa, "salt": n_salt,
                    "protein": p_protein, "fibre": p_fibre, "fruit_veg": p_fruit_veg
                }
            }
        }

    def get_grade(self, score, is_beverage=False, is_water=False):
        if is_beverage:
            # Water is the only beverage that can achieve Grade A
            if is_water:
                return "A"
            if score <= 2: return "B"
            if score <= 6: return "C"
            if score <= 9: return "D"
            return "E"
        else:
            if score <= 0: return "A"
            if score <= 2: return "B"
            if score <= 10: return "C"
            if score <= 18: return "D"
            return "E"

# Singleton instance
calculator = NutriScoreV7()
