import math

class NutriScoreV7:
    """
    Nutri-Score 2023 (V7) Deterministic Calculator
    Based on the official FAQ V7 (Dec 2023)
    """

    def __init__(self):
        # N-points thresholds (Upper bounds for each point)
        self.energy_thresholds = [335 * i for i in range(1, 11)]  # 335, 670, ... 3350
        self.sugar_thresholds = [3.4, 6.8, 10, 14, 17, 20, 24, 27, 31, 34, 37, 41, 44, 48, 51]
        self.sfa_thresholds = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
        self.salt_thresholds = [0.2, 0.4, 0.6, 0.8, 1.0, 1.2, 1.4, 1.6, 1.8, 2.0, 2.2, 2.4, 2.6, 2.8, 3.0, 3.2, 3.4, 3.6, 3.8, 4.0]

        # P-points thresholds
        self.protein_thresholds = [2.4, 4.8, 7.2, 9.6, 12, 14, 17]
        self.fibre_thresholds = [3.0, 4.1, 5.2, 6.3, 7.4]
        self.fruit_veg_thresholds = [40, 60, 80]

    def _get_points(self, value, thresholds):
        points = 0
        for t in thresholds:
            if value > t:
                points += 1
            else:
                break
        return points

    def calculate(self, data, is_beverage=False, is_cheese=False, is_red_meat=False, has_sweeteners=False):
        """
        data: { energy, sugars, sfa, salt, proteins, fibres, fruit_veg_pct }
        salt is in grams. energy in kJ.
        """
        # 1. Calculate N components
        n_energy = self._get_points(data.get('energy', 0), self.energy_thresholds)
        n_sugars = self._get_points(data.get('sugars', 0), self.sugar_thresholds)
        n_sfa = self._get_points(data.get('sfa', 0), self.sfa_thresholds)
        n_salt = self._get_points(data.get('salt', 0), self.salt_thresholds)
        
        points_n = n_energy + n_sugars + n_sfa + n_salt
        
        # 2. Calculate P components
        p_protein = self._get_points(data.get('proteins', 0), self.protein_thresholds)
        p_fibre = self._get_points(data.get('fibres', 0), self.fibre_thresholds)
        p_fruit_veg = 0
        fv_pct = data.get('fruit_veg_pct', 0)
        if fv_pct > 80: p_fruit_veg = 5
        elif fv_pct > 60: p_fruit_veg = 2
        elif fv_pct > 40: p_fruit_veg = 1
        
        # Red meat exception: max protein points = 2
        if is_red_meat:
            p_protein = min(p_protein, 2)
            
        points_p = p_protein + p_fibre + p_fruit_veg

        # 3. Final Score logic (Decision Tree)
        if points_n < 11 or is_cheese:
            score = points_n - points_p
        else:
            # Special case for N >= 11
            if p_fruit_veg == 5:
                score = points_n - points_p
            else:
                score = points_n - (p_fibre + p_fruit_veg)

        # 4. Grade Attribution
        grade = self.get_grade(score, is_beverage)
        
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

    def get_grade(self, score, is_beverage=False):
        if is_beverage:
            # Simplified beverage thresholds for V7
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
