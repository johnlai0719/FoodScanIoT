def get_barcode_info(barcode: str):
    """
    解析 EAN-13 條碼的初步資訊 (根據 GS1 完整前綴表)
    """
    if not barcode or len(barcode) < 13:
        return {"valid": False, "msg": "條碼格式錯誤", "registered_country": "未知"}

    # 取前三碼 (GS1 Prefix)
    prefix_str = barcode[:3]
    try:
        prefix = int(prefix_str)
    except:
        return {"valid": False, "msg": "條碼內容非數字", "registered_country": "未知"}
    
    country = "未知國家/區域"

    # 根據範圍判斷國家或用途
    if 0 <= prefix <= 19: country = "美國 (GS1 US)"
    elif 20 <= prefix <= 29: country = "店內碼 (Restricted distribution)"
    elif 30 <= prefix <= 39: country = "美國 (GS1 US)"
    elif 40 <= prefix <= 49: country = "店內碼 (Restricted distribution)"
    elif 50 <= prefix <= 59: country = "優惠券 (Coupons)"
    elif 60 <= prefix <= 139: country = "美國 (GS1 US)"
    elif 200 <= prefix <= 299: country = "店內碼 (Restricted distribution)"
    elif 300 <= prefix <= 379: country = "法國 (GS1 France)"
    elif prefix == 380: country = "保加利亞"
    elif prefix == 383: country = "斯洛維尼亞"
    elif prefix == 385: country = "克羅埃西亞"
    elif prefix == 387: country = "波士尼亞"
    elif prefix == 389: country = "蒙特內歌羅"
    elif 400 <= prefix <= 440: country = "德國 (GS1 Germany)"
    elif (450 <= prefix <= 459) or (490 <= prefix <= 499): country = "日本 (GS1 Japan)"
    elif 460 <= prefix <= 469: country = "俄羅斯"
    elif prefix == 470: country = "吉爾吉斯"
    elif prefix == 471: country = "台灣 (GS1 Taiwan)"
    elif prefix == 474: country = "愛沙尼亞"
    elif prefix == 475: country = "拉脫維亞"
    elif prefix == 476: country = "阿塞拜疆"
    elif prefix == 477: country = "立陶宛"
    elif prefix == 478: country = "烏茲別克"
    elif prefix == 479: country = "斯里蘭卡"
    elif prefix == 480: country = "菲律賓"
    elif prefix == 481: country = "白俄羅斯"
    elif prefix == 482: country = "烏克蘭"
    elif prefix == 483: country = "土庫曼"
    elif prefix == 484: country = "摩爾達維亞"
    elif prefix == 485: country = "亞美尼亞"
    elif prefix == 486: country = "喬治亞"
    elif prefix == 487: country = "哈薩克"
    elif prefix == 488: country = "塔吉克斯坦"
    elif prefix == 489: country = "香港"
    elif 500 <= prefix <= 509: country = "英國 (GS1 UK)"
    elif 520 <= prefix <= 521: country = "希臘"
    elif prefix == 528: country = "黎巴嫩"
    elif prefix == 529: country = "賽普勒斯"
    elif prefix == 530: country = "阿爾巴尼亞"
    elif prefix == 531: country = "馬其頓"
    elif prefix == 535: country = "馬爾他"
    elif prefix == 539: country = "愛爾蘭"
    elif 540 <= prefix <= 549: country = "比利時.盧森堡"
    elif prefix == 560: country = "葡萄牙"
    elif prefix == 569: country = "冰島"
    elif 570 <= prefix <= 579: country = "丹麥"
    elif prefix == 590: country = "波蘭"
    elif prefix == 594: country = "羅馬尼亞"
    elif prefix == 599: country = "匈牙利"
    elif 600 <= prefix <= 601: country = "南非"
    elif prefix == 603: country = "加納"
    elif prefix == 604: country = "塞內加爾"
    elif prefix == 608: country = "巴林"
    elif prefix == 609: country = "摩里西斯"
    elif prefix == 611: country = "摩洛哥"
    elif prefix == 613: country = "阿爾及利亞"
    elif prefix == 615: country = "奈及利亞"
    elif prefix == 616: country = "肯亞"
    elif prefix == 617: country = "喀麥隆"
    elif prefix == 618: country = "象牙海岸"
    elif prefix == 619: country = "突尼西亞"
    elif prefix == 620: country = "坦尚尼亞"
    elif prefix == 621: country = "敘利亞"
    elif prefix == 622: country = "埃及"
    elif prefix == 623: country = "汶萊"
    elif prefix == 624: country = "利比亞"
    elif prefix == 625: country = "約旦"
    elif prefix == 626: country = "伊朗"
    elif prefix == 627: country = "科威特"
    elif prefix == 628: country = "沙烏地阿拉伯"
    elif prefix == 629: country = "阿聯酋"
    elif prefix == 630: country = "卡塔爾"
    elif prefix == 631: country = "納米比亞"
    elif 640 <= prefix <= 649: country = "芬蘭"
    elif 690 <= prefix <= 699: country = "中國大陸"
    elif 700 <= prefix <= 709: country = "挪威"
    elif prefix == 729: country = "以色列"
    elif 730 <= prefix <= 739: country = "瑞典"
    elif prefix == 740: country = "瓜地馬拉"
    elif prefix == 741: country = "薩爾瓦多"
    elif prefix == 742: country = "宏都拉斯"
    elif prefix == 743: country = "尼加拉瓜"
    elif prefix == 744: country = "哥斯大黎加"
    elif prefix == 745: country = "巴拿馬"
    elif prefix == 746: country = "多明尼加"
    elif prefix == 750: country = "墨西哥"
    elif 754 <= prefix <= 755: country = "加拿大"
    elif prefix == 759: country = "委內瑞拉"
    elif 760 <= prefix <= 769: country = "瑞士"
    elif 770 <= prefix <= 771: country = "哥倫比亞"
    elif prefix == 773: country = "烏拉圭"
    elif prefix == 775: country = "祕魯"
    elif prefix == 777: country = "波利維亞"
    elif 778 <= prefix <= 779: country = "阿根廷"
    elif prefix == 780: country = "智利"
    elif prefix == 784: country = "巴拉圭"
    elif prefix == 786: country = "厄圭多爾"
    elif 789 <= prefix <= 790: country = "巴西"
    elif 800 <= prefix <= 839: country = "義大利"
    elif 840 <= prefix <= 849: country = "西班牙"
    elif prefix == 850: country = "古巴"
    elif prefix == 858: country = "斯洛伐克"
    elif prefix == 859: country = "捷克"
    elif prefix == 860: country = "塞爾維亞"
    elif prefix == 865: country = "蒙古"
    elif prefix == 867: country = "北韓"
    elif 868 <= prefix <= 869: country = "土耳其"
    elif 870 <= prefix <= 879: country = "荷蘭"
    elif prefix == 880: country = "南韓"
    elif prefix == 884: country = "柬埔塞"
    elif prefix == 885: country = "泰國"
    elif prefix == 888: country = "新加坡"
    elif prefix == 890: country = "印度"
    elif prefix == 893: country = "越南"
    elif prefix == 896: country = "巴基斯坦"
    elif prefix == 899: country = "印尼"
    elif 900 <= prefix <= 919: country = "奧地利"
    elif 930 <= prefix <= 939: country = "澳洲"
    elif 940 <= prefix <= 949: country = "紐西蘭"
    elif prefix == 950: country = "GS1 總會"
    elif prefix == 951: country = "EPCglobal"
    elif prefix == 955: country = "馬來西亞"
    elif prefix == 958: country = "澳門"
    elif 960 <= prefix <= 969: country = "GTIN-8s 縮短碼"
    elif prefix == 977: country = "期刊 (ISSN)"
    elif 978 <= prefix <= 979: country = "書籍 (ISBN)"
    elif prefix == 980: country = "退款收據"
    elif 981 <= prefix <= 984: country = "貨幣票券"
    elif 990 <= prefix <= 999: country = "禮券 (Coupons)"

    # 簡易檢查碼驗證 (Check Digit)
    try:
        odd_sum = sum(int(barcode[i]) for i in range(0, 12, 2))
        even_sum = sum(int(barcode[i]) for i in range(1, 12, 2))
        total = odd_sum + (even_sum * 3)
        check_digit = (10 - (total % 10)) % 10
        is_valid = check_digit == int(barcode[12])
    except:
        is_valid = False

    return {
        "valid": is_valid,
        "prefix": prefix_str,
        "registered_country": country,
        "manufacturer_code": barcode[3:9],
        "product_code": barcode[9:12]
    }
