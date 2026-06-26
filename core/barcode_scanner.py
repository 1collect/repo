from pathlib import Path

EAN13_LEFT_ODD = {
    "0001101": "0",
    "0011001": "1",
    "0010011": "2",
    "0111101": "3",
    "0100011": "4",
    "0110001": "5",
    "0101111": "6",
    "0111011": "7",
    "0110111": "8",
    "0001011": "9",
}
EAN13_LEFT_EVEN = {
    "0100111": "0",
    "0110011": "1",
    "0011011": "2",
    "0100001": "3",
    "0011101": "4",
    "0111001": "5",
    "0000101": "6",
    "0010001": "7",
    "0001001": "8",
    "0010111": "9",
}
EAN13_RIGHT = {
    "1110010": "0",
    "1100110": "1",
    "1101100": "2",
    "1000010": "3",
    "1011100": "4",
    "1001110": "5",
    "1010000": "6",
    "1000100": "7",
    "1001000": "8",
    "1110100": "9",
}
EAN13_PARITY = {
    "LLLLLL": "0",
    "LLGLGG": "1",
    "LLGGLG": "2",
    "LLGGGL": "3",
    "LGLLGG": "4",
    "LGGLLG": "5",
    "LGGGLL": "6",
    "LGLGLG": "7",
    "LGLGGL": "8",
    "LGGLGL": "9",
}


def decode_barcode_from_image(file_path):
    """Return the first barcode/QR value decoded from an image file, or None."""
    path = Path(file_path)
    if not path.exists():
        return None

    for value in _decode_with_libraries(path):
        return value

    try:
        from PIL import Image

        with Image.open(path) as image:
            return _decode_ean13_from_image(image)
    except Exception:
        return None


def _decode_with_libraries(path):
    for image in _image_variants(path):
        value = _decode_with_zxing(image)
        if value:
            yield value
            return
        value = _decode_with_pyzbar(image)
        if value:
            yield value
            return


def _image_variants(path):
    try:
        from PIL import Image, ImageEnhance, ImageFilter, ImageOps

        with Image.open(path) as source:
            image = ImageOps.exif_transpose(source).convert("RGB")
            variants = [image]
            gray = ImageOps.grayscale(image)
            variants.append(gray)
            variants.append(ImageOps.autocontrast(gray))
            variants.append(ImageEnhance.Contrast(gray).enhance(2.0))
            variants.append(gray.filter(ImageFilter.SHARPEN))
            variants.append(_crop_probable_barcode(gray))
            for item in list(variants):
                if item is None:
                    continue
                yield item
                width, height = item.size
                if max(width, height) < 2400:
                    yield item.resize((width * 2, height * 2))
                thresholded = _threshold_image(item)
                if thresholded is not None:
                    yield thresholded
    except Exception:
        return


def _decode_with_zxing(image):
    try:
        import zxingcpp

        results = zxingcpp.read_barcodes(image)
        for result in results:
            value = str(getattr(result, "text", "") or "").strip()
            if value:
                return value
    except Exception:
        return None
    return None


def _decode_with_pyzbar(image):
    try:
        from pyzbar.pyzbar import decode

        for code in decode(image):
            value = code.data.decode("utf-8", errors="ignore").strip()
            if value:
                return value
    except Exception:
        return None
    return None


def _threshold_image(image):
    try:
        from PIL import ImageOps

        gray = ImageOps.grayscale(image)
        threshold = _otsu_threshold(gray)
        return gray.point(lambda value: 0 if value < threshold else 255, mode="1")
    except Exception:
        return None


def _crop_probable_barcode(image):
    try:
        from PIL import ImageOps

        gray = ImageOps.grayscale(image)
        threshold = _otsu_threshold(gray)
        width, height = gray.size
        pixels = gray.load()
        row_scores = []
        for y in range(height):
            dark = 0
            for x in range(width):
                if pixels[x, y] < threshold:
                    dark += 1
            row_scores.append(dark)
        max_score = max(row_scores) if row_scores else 0
        if max_score <= 0:
            return gray
        row_limit = max(max_score * 0.35, width * 0.08)
        bands = _contiguous_ranges([idx for idx, score in enumerate(row_scores) if score >= row_limit], gap=8)
        if not bands:
            return gray
        y1, y2 = max(bands, key=lambda item: item[1] - item[0])
        pad_y = max(8, (y2 - y1) // 5)
        y1 = max(0, y1 - pad_y)
        y2 = min(height - 1, y2 + pad_y)

        col_scores = []
        for x in range(width):
            dark = 0
            for y in range(y1, y2 + 1):
                if pixels[x, y] < threshold:
                    dark += 1
            col_scores.append(dark)
        max_col = max(col_scores) if col_scores else 0
        if max_col <= 0:
            return gray.crop((0, y1, width, y2 + 1))
        col_limit = max(max_col * 0.25, (y2 - y1 + 1) * 0.05)
        xs = [idx for idx, score in enumerate(col_scores) if score >= col_limit]
        if not xs:
            return gray.crop((0, y1, width, y2 + 1))
        x1, x2 = min(xs), max(xs)
        pad_x = max(20, (x2 - x1) // 15)
        x1 = max(0, x1 - pad_x)
        x2 = min(width - 1, x2 + pad_x)
        return gray.crop((x1, y1, x2 + 1, y2 + 1))
    except Exception:
        return image


def _decode_ean13_from_image(image):
    try:
        from PIL import ImageOps

        gray = ImageOps.grayscale(image)
        crops = [gray, _crop_probable_barcode(gray)]
        for crop in crops:
            if crop is None:
                continue
            scaled = crop
            if max(scaled.size) < 1800:
                scaled = scaled.resize((scaled.size[0] * 2, scaled.size[1] * 2))
            for candidate in _ean13_candidates_from_crop(scaled):
                if _valid_ean13(candidate):
                    return candidate
    except Exception:
        return None
    return None


def _ean13_candidates_from_crop(image):
    gray = image.convert("L")
    threshold = _otsu_threshold(gray)
    width, height = gray.size
    pixels = gray.load()
    row_positions = [height // 3, height // 2, (height * 2) // 3]
    offsets = [-4, -2, 0, 2, 4]
    for row in row_positions:
        for offset in offsets:
            y = min(height - 1, max(0, row + offset))
            binary = [1 if pixels[x, y] < threshold else 0 for x in range(width)]
            if sum(binary) < width * 0.08:
                continue
            black_indexes = [idx for idx, value in enumerate(binary) if value]
            if not black_indexes:
                continue
            left = min(black_indexes)
            right = max(black_indexes)
            for pad_left in range(-8, 9, 2):
                for pad_right in range(-8, 9, 2):
                    x1 = max(0, left + pad_left)
                    x2 = min(width - 1, right + pad_right)
                    if x2 <= x1:
                        continue
                    bits = _sample_bits(binary, x1, x2, 95)
                    decoded = _decode_ean13_bits(bits)
                    if decoded:
                        yield decoded


def _sample_bits(binary, x1, x2, length):
    span = x2 - x1 + 1
    bits = []
    for idx in range(length):
        x = int(round(x1 + (idx + 0.5) * span / length))
        x = min(len(binary) - 1, max(0, x))
        bits.append("1" if binary[x] else "0")
    return "".join(bits)


def _decode_ean13_bits(bits):
    if len(bits) != 95:
        return None
    if bits[:3] != "101" or bits[45:50] != "01010" or bits[-3:] != "101":
        return None
    left_digits = []
    parity = []
    for index in range(6):
        pattern = bits[3 + index * 7 : 10 + index * 7]
        if pattern in EAN13_LEFT_ODD:
            left_digits.append(EAN13_LEFT_ODD[pattern])
            parity.append("L")
        elif pattern in EAN13_LEFT_EVEN:
            left_digits.append(EAN13_LEFT_EVEN[pattern])
            parity.append("G")
        else:
            return None
    first_digit = EAN13_PARITY.get("".join(parity))
    if first_digit is None:
        return None
    right_digits = []
    for index in range(6):
        pattern = bits[50 + index * 7 : 57 + index * 7]
        digit = EAN13_RIGHT.get(pattern)
        if digit is None:
            return None
        right_digits.append(digit)
    value = first_digit + "".join(left_digits) + "".join(right_digits)
    return value if _valid_ean13(value) else None


def _valid_ean13(value):
    value = str(value or "")
    if len(value) != 13 or not value.isdigit():
        return False
    total = 0
    for index, char in enumerate(value[:12]):
        total += int(char) * (1 if index % 2 == 0 else 3)
    checksum = (10 - total % 10) % 10
    return checksum == int(value[-1])


def _otsu_threshold(image):
    histogram = image.histogram()
    total = sum(histogram)
    if total <= 0:
        return 128
    sum_total = sum(index * count for index, count in enumerate(histogram))
    sum_background = 0
    weight_background = 0
    best_threshold = 128
    best_variance = 0
    for threshold, count in enumerate(histogram):
        weight_background += count
        if weight_background == 0:
            continue
        weight_foreground = total - weight_background
        if weight_foreground == 0:
            break
        sum_background += threshold * count
        mean_background = sum_background / weight_background
        mean_foreground = (sum_total - sum_background) / weight_foreground
        variance = weight_background * weight_foreground * (mean_background - mean_foreground) ** 2
        if variance > best_variance:
            best_variance = variance
            best_threshold = threshold
    return best_threshold


def _contiguous_ranges(values, gap=0):
    if not values:
        return []
    ranges = []
    start = previous = values[0]
    for value in values[1:]:
        if value - previous <= gap + 1:
            previous = value
            continue
        ranges.append((start, previous))
        start = previous = value
    ranges.append((start, previous))
    return ranges