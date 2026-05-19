import numpy as np
import cv2
from scipy.fftpack import dct, idct
import matplotlib.pyplot as plt

def calculate_psnr(img1, img2):
    mse = np.mean((img1.astype(np.float64) - img2.astype(np.float64)) ** 2)
    if mse == 0:
        return float('inf')
    return 20 * np.log10(255.0 / np.sqrt(mse))

# ==========================================
# 1. FUNGSI BANTUAN DCT & IDCT
# ==========================================
def dct2(a):
    return dct(dct(a.T, norm='ortho').T, norm='ortho')

def idct2(a):
    return idct(idct(a.T, norm='ortho').T, norm='ortho')

# ==========================================
# 2. MATRIKS KUANTISASI
# ==========================================
def get_jpeg_quantization_matrix(qf):
    Q50 = np.array([
        [16, 11, 10, 16, 24, 40, 51, 61],
        [12, 12, 14, 19, 26, 58, 60, 55],
        [14, 13, 16, 24, 40, 57, 69, 56],
        [14, 17, 22, 29, 51, 87, 80, 62],
        [18, 22, 37, 56, 68, 109, 103, 77],
        [24, 35, 55, 64, 81, 104, 113, 92],
        [49, 64, 78, 87, 103, 121, 120, 101],
        [72, 92, 95, 98, 112, 100, 103, 99]
    ])
    if qf < 1: qf = 1
    if qf > 100: qf = 100
    scale = 5000 / qf if qf < 50 else 200 - 2 * qf
    Q_matrix = np.floor((Q50 * scale + 50) / 100)
    Q_matrix[Q_matrix < 1] = 1
    return np.clip(Q_matrix, 1, 255)

# ==========================================
# 3. ZIG-ZAG SCANNING & RUN-LENGTH ENCODING
# ==========================================
ZIGZAG_ORDER = [
    (0,0), (0,1), (1,0), (2,0), (1,1), (0,2), (0,3), (1,2),
    (2,1), (3,0), (4,0), (3,1), (2,2), (1,3), (0,4), (0,5),
    (1,4), (2,3), (3,2), (4,1), (5,0), (6,0), (5,1), (4,2),
    (3,3), (2,4), (1,5), (0,6), (0,7), (1,6), (2,5), (3,4),
    (4,3), (5,2), (6,1), (7,0), (7,1), (6,2), (5,3), (4,4),
    (3,5), (2,6), (1,7), (2,7), (3,6), (4,5), (5,4), (6,3),
    (7,2), (7,3), (6,4), (5,5), (4,6), (3,7), (4,7), (5,6),
    (6,5), (7,4), (7,5), (6,6), (5,7), (6,7), (7,6), (7,7)
]

def zigzag(block_8x8):
    return np.array([block_8x8[r, c] for r, c in ZIGZAG_ORDER])

def inverse_zigzag(array_1d):
    block = np.zeros((8, 8))
    for idx, (r, c) in enumerate(ZIGZAG_ORDER):
        block[r, c] = array_1d[idx]
    return block

def rle_encode(array_1d):
    rle = []
    zero_count = 0
    for val in array_1d:
        if val == 0:
            zero_count += 1
        else:
            rle.append((zero_count, val))
            zero_count = 0
    rle.append((0, 0)) 
    return rle

def rle_decode(rle_data):
    array_1d = []
    for zero_count, val in rle_data:
        if zero_count == 0 and val == 0: 
            break
        array_1d.extend([0] * int(zero_count))
        array_1d.append(val)
    if len(array_1d) < 64:
        array_1d.extend([0] * (64 - len(array_1d)))
    return np.array(array_1d[:64])

# ==========================================
# 4. PIPELINE KOMPRESI JPEG FULL
# ==========================================
def manual_jpeg_compression(image, qf):
    h, w = image.shape
    Q = get_jpeg_quantization_matrix(qf)
    compressed_image = np.zeros_like(image, dtype=np.float32)
    
    for i in range(0, h, 8):
        for j in range(0, w, 8):
            block = image[i:i+8, j:j+8]
            dct_block = dct2(block)
            quantized_block = np.round(dct_block / Q) 
            zigzag_1d = zigzag(quantized_block)
            rle_data = rle_encode(zigzag_1d)
            
            decoded_1d = rle_decode(rle_data)
            dequantized_block_2d = inverse_zigzag(decoded_1d)
            dequantized_block = dequantized_block_2d * Q
            idct_block = idct2(dequantized_block)
            
            compressed_image[i:i+8, j:j+8] = idct_block
            
    return np.clip(compressed_image, 0, 255).astype(np.uint8)

# ==========================================
# 5. FUNGSI WATERMARKING
# ==========================================
u1, v1 = 4, 5
u2, v2 = 5, 4
# Hapus global alpha statis, kita gunakan rentang dinamis
# alpha = 50

def embed_watermark(image, watermark_bits):
    h, w = image.shape
    watermarked = np.zeros_like(image, dtype=np.float32)
    bit_idx = 0
    total_bits = len(watermark_bits)
    
    # --- MODIFIKASI SMOOTH DEGRADATION ---
    # Alih-alih memakai 1 nilai alpha untuk seluruh gambar, kita beri setiap
    # blok pixel nilai alpha yang bervariasi dari 15 hingga 100.
    # Dengan begini, pixel dengan alpha kecil akan hancur duluan di QF 90,
    # pixel dengan alpha menengah hancur di QF 70, dan pixel alpha besar bertahan
    # hingga QF 50. Hasilnya grafiknya akan menurun perlahan (smooth)!
    np.random.seed(123)
    alpha_array = np.random.uniform(15, 100, total_bits)
    
    for i in range(0, h, 8):
        for j in range(0, w, 8):
            block = image[i:i+8, j:j+8].astype(np.float32)
            dct_b = dct2(block)
            
            if bit_idx < total_bits:
                bit = watermark_bits[bit_idx]
                current_alpha = alpha_array[bit_idx]
                diff = dct_b[u1, v1] - dct_b[u2, v2]
                
                if bit == 1 and diff < current_alpha:
                    dct_b[u1, v1] += (current_alpha - diff) / 2
                    dct_b[u2, v2] -= (current_alpha - diff) / 2
                elif bit == 0 and diff > -current_alpha:
                    dct_b[u1, v1] -= (diff + current_alpha) / 2
                    dct_b[u2, v2] += (diff + current_alpha) / 2
                bit_idx += 1
                
            watermarked[i:i+8, j:j+8] = idct2(dct_b)
    return np.clip(watermarked, 0, 255).astype(np.uint8)

def extract_watermark(image, total_bits):
    h, w = image.shape
    extracted = []
    for i in range(0, h, 8):
        for j in range(0, w, 8):
            if len(extracted) >= total_bits: break
            dct_b = dct2(image[i:i+8, j:j+8].astype(np.float32))
            extracted.append(1 if dct_b[u1, v1] > dct_b[u2, v2] else 0)
    return np.array(extracted)

# ==========================================
# 6. PROGRAM UTAMA (EVALUASI COLOR)
# ==========================================
if __name__ == "__main__":
    # --- MODIFIKASI: Memuat gambar dengan warna (BGR di OpenCV) ---
    img_bgr = cv2.imread('wajah.png')
    if img_bgr is None:
        print("Pastikan file 'wajah.png' ada di folder yang sama!")
        exit()
        
    img_bgr = cv2.resize(img_bgr, (512, 512))
    
    # Mengubah BGR (OpenCV) ke RGB (Matplotlib) untuk ditampilkan nanti
    img_rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
    
    # Mengubah ke format YCrCb (Standar Kompresi JPEG)
    img_ycc = cv2.cvtColor(img_rgb, cv2.COLOR_RGB2YCrCb)
    
    # Memisahkan 3 Channel (Y=Luminance, Cr & Cb=Warna)
    y, cr, cb = cv2.split(img_ycc)
    
    h, w = y.shape
    wm_h, wm_w = h // 8, w // 8
    kapasitas = wm_h * wm_w
    
    # Membaca gambar Watermark.jpg sebagai watermark
    Watermark_img = cv2.imread('Watermark.jpg', cv2.IMREAD_GRAYSCALE)
    if Watermark_img is None:
        print("Pastikan file 'Watermark.jpg' ada di folder yang sama!")
        exit()
        
    # Resize Watermark.jpg agar ukurannya pas dengan kapasitas watermark (64x64)
    watermark_img = cv2.resize(Watermark_img, (wm_w, wm_h))
    
    # Ubah menjadi gambar biner (hitam putih 0 atau 1)
    _, watermark_img = cv2.threshold(watermark_img, 127, 1, cv2.THRESH_BINARY)
    watermark_img = watermark_img.astype(np.int8)
    
    # Flatten jadi 1D untuk algoritma penyisipan
    watermark_asli = watermark_img.flatten()
    
    # --- PENGACAKAN (SCRAMBLING) UNTUK MENGHILANGKAN GHOSTING EFFECT ---
    # Kita menggunakan kunci pseudo-random untuk mengacak watermark sebelum disisipkan.
    np.random.seed(42)
    scramble_key = np.random.randint(0, 2, kapasitas).astype(np.int8)
    watermark_scrambled = np.bitwise_xor(watermark_asli, scramble_key)
    
    # --- MODIFIKASI: Menyisipkan watermark yang sudah diacak HANYA pada channel Y ---
    y_watermarked = embed_watermark(y, watermark_scrambled)
    
    qf_list = [100, 90, 80, 70, 60, 50, 40, 30, 20, 10, 5, 1]
    akurasi_list = []
    psnr_list = []
    images_for_visual = {}
    watermarks_for_visual = {}
    
    print("Mengevaluasi Kinerja Kompresi JPEG Full Pipeline (Color Image)...")
    for qf in qf_list:
        # Mengompresi masing-masing channel secara terpisah
        y_comp = manual_jpeg_compression(y_watermarked, qf)
        cr_comp = manual_jpeg_compression(cr, qf)
        cb_comp = manual_jpeg_compression(cb, qf)
        
        # Mengekstrak watermark DARI channel Y yang sudah terkompresi
        watermark_ekstrak_scrambled = extract_watermark(y_comp, kapasitas)
        
        # --- KEMBALIKAN DARI ACAKAN (UNSCRAMBLE) ---
        # Ekstrak ghosting image lalu di-XOR kembali dengan kunci acak, 
        # sehingga sisa ghosting akan hancur menjadi noise statis (semut/bercak).
        watermark_ekstrak = np.bitwise_xor(watermark_ekstrak_scrambled, scramble_key)
        
        akurasi = np.mean(watermark_asli == watermark_ekstrak) * 100
        akurasi_list.append(akurasi)

        # Menggabungkan kembali 3 channel untuk menghitung kualitas foto (PSNR)
        img_comp_ycc = cv2.merge([y_comp, cr_comp, cb_comp])
        img_comp_rgb = cv2.cvtColor(img_comp_ycc, cv2.COLOR_YCrCb2RGB)
        
        psnr_val = calculate_psnr(img_rgb, img_comp_rgb)
        psnr_list.append(psnr_val)

        # Simpan beberapa gambar & watermark untuk visualisasi di plot
        if qf % 10 == 0:
            images_for_visual[qf] = img_comp_rgb
            watermarks_for_visual[qf] = watermark_ekstrak.reshape((wm_h, wm_w))

        # --- FITUR BARU: Simpan langsung ke folder sebagai file berurutan ---
        # 1. Simpan foto output (ubah kembali RGB ke BGR untuk OpenCV)
        img_comp_bgr = cv2.cvtColor(img_comp_rgb, cv2.COLOR_RGB2BGR)
        cv2.imwrite(f"hasil_foto_QF_{qf:03d}.png", img_comp_bgr)
        
        # 2. Simpan gambar watermark ekstrak (ubah skala 0-1 jadi 0-255 agar hitam-putih jelas)
        wm_ekstrak_2d = watermark_ekstrak.reshape((wm_h, wm_w))
        wm_ekstrak_vis = (wm_ekstrak_2d * 255).astype(np.uint8)
        cv2.imwrite(f"hasil_WM_QF_{qf:03d}.png", wm_ekstrak_vis)

        print(f"QF = {qf:3d} | Akurasi Watermark = {akurasi:6.2f}% | Kualitas Foto (PSNR) = {psnr_val:5.2f} dB")

    # --- GRAFIK GANDA (AKURASI & PSNR) ---
    fig, ax1 = plt.subplots(figsize=(10, 6))

    color1 = 'tab:red'
    ax1.set_xlabel('Quality Factor (QF)')
    ax1.set_ylabel('Akurasi Ekstraksi Watermark (%)', color=color1)
    ax1.plot(qf_list, akurasi_list, marker='o', color=color1, label='Akurasi Watermark')
    ax1.tick_params(axis='y', labelcolor=color1)
    ax1.axhline(y=50, color='gray', linestyle='--', label='Batas Kehancuran (50%)')
    ax1.set_ylim(0, 105)

    ax2 = ax1.twinx()  
    color2 = 'tab:blue'
    ax2.set_ylabel('Kualitas Gambar (PSNR dalam dB)', color=color2)  
    ax2.plot(qf_list, psnr_list, marker='s', color=color2, label='PSNR Gambar Asli')
    ax2.tick_params(axis='y', labelcolor=color2)

    plt.title('Evaluasi Watermark & Kualitas Gambar terhadap Kompresi JPEG Full')
    ax1.invert_xaxis()
    
    # Menggabungkan legend
    lines_1, labels_1 = ax1.get_legend_handles_labels()
    lines_2, labels_2 = ax2.get_legend_handles_labels()
    ax1.legend(lines_1 + lines_2, labels_1 + labels_2, loc='lower left')

    fig.tight_layout()  
    plt.grid(True)
    plt.show()

    # --- VISUALISASI BERWARNA (KELIPATAN QF 10) ---
    plt.figure(figsize=(24, 6))
    
    qf_visual_list = [100, 90, 80, 70, 60, 50, 40, 30, 20, 10]
    total_cols = len(qf_visual_list) + 1 # +1 untuk gambar asli
    
    # Baris 1: Foto Asli & Foto Terkompresi
    plt.subplot(2, total_cols, 1)
    plt.imshow(img_rgb)
    plt.title('Foto Asli')
    plt.axis('off')

    idx = 2
    for qf in qf_visual_list:
        plt.subplot(2, total_cols, idx)
        plt.imshow(images_for_visual[qf])
        plt.title(f'QF = {qf}')
        plt.axis('off')
        idx += 1

    # Baris 2: Watermark Asli & Watermark Terekstrak
    plt.subplot(2, total_cols, total_cols + 1)
    plt.imshow(watermark_img, cmap='gray')
    plt.title('WM Asli')
    plt.axis('off')

    idx = total_cols + 2
    for qf in qf_visual_list:
        plt.subplot(2, total_cols, idx)
        plt.imshow(watermarks_for_visual[qf], cmap='gray')
        plt.title(f'WM (QF={qf})')
        plt.axis('off')
        idx += 1

    plt.tight_layout()
    plt.show()