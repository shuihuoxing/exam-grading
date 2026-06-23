"""生成两张样例图：学生答卷 + 标准答案（打印体，便于 OCR）。
输出到 _samples/ 目录。
"""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parents[2] / "_samples"
OUT.mkdir(parents=True, exist_ok=True)


def font(size):
    for name in ["msyh.ttc", "simhei.ttf", "simsun.ttc", "arial.ttf"]:
        p = Path("C:/Windows/Fonts") / name
        if p.exists():
            return ImageFont.truetype(str(p), size)
    return ImageFont.load_default()


def new_sheet():
    img = Image.new("RGB", (1240, 1754), "white")  # ~A4 @150dpi
    return img, ImageDraw.Draw(img)


def main():
    f_title = font(48)
    f_body = font(40)

    # ---- 学生答卷 ----
    img, d = new_sheet()
    y = 60
    d.text((60, y), "数学与常识 测试卷  姓名：张三", font=f_title, fill="black"); y += 100
    lines = [
        "1. 2 + 2 = ?    A. 3    B. 4    C. 5      我的答案：B",
        "2. 法国首都是？ A. 伦敦  B. 巴黎  C. 柏林   我的答案：C",
        "3. 判断题：太阳是恒星。   我的答案：对",
        "4. 填空题：5 × 6 = 30",
        "5. 简答题：简述光合作用。",
        "植物利用阳光合成有机物。",
    ]
    for ln in lines:
        d.text((60, y), ln, font=f_body, fill="black"); y += 90
    stu_path = OUT / "student.png"
    img.save(stu_path, "PNG")

    # ---- 标准答案 ----
    img, d = new_sheet()
    y = 60
    d.text((60, y), "标准答案", font=f_title, fill="black"); y += 100
    ans = [
        "1. 选择题  答案：B",
        "2. 选择题  答案：B",
        "3. 判断题  答案：对",
        "4. 填空题  答案：30",
        "5. 简答题  答案：植物在叶绿体中利用光能将二氧化碳和水转化为有机物并释放氧气。",
        "评分细则：提到光能 3分；提到二氧化碳和水 3分；提到有机物/氧气 4分。",
    ]
    for ln in ans:
        d.text((60, y), ln, font=f_body, fill="black"); y += 90
    ans_path = OUT / "answer.png"
    img.save(ans_path, "PNG")

    print("生成：", stu_path, ans_path)


if __name__ == "__main__":
    main()
