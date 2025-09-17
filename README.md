# ESO Price Tracker

Elder Scrolls Online için otomatik fiyat takip botu. TTC (Tamriel Trade Centre) üzerinden item fiyatlarını kontrol eder ve belirlediğiniz eşiklerin altında item bulduğunda Telegram üzerinden bildirim gönderir.

## Özellikler

- 🔄 Otomatik fiyat kontrolü (5 dakikada bir)
- 📱 Telegram bildirimleri
- 🎯 Kullanıcı tanımlı fiyat eşikleri
- 🤖 Captcha bypass sistemi
- 📊 Detaylı fiyat analizi
- 👥 Çoklu kullanıcı desteği

## Hızlı Başlangıç

### 1. İndirme

- Bu sayfanın üstünde yeşil "Code" butonuna tıklayın
- "Download ZIP" seçeneğini seçin
- ZIP dosyasını istediğiniz yere çıkarın

### 2. Telegram Bot Oluşturma

1. Telegram'da [@BotFather](https://t.me/BotFather) botuna mesaj atın
2. `/newbot` komutunu yazın
3. Bot için bir isim verin (örnek: "Ahmet ESO Tracker")
4. Bot için bir username verin (örnek: "ahmet_eso_bot")
5. Size verilen token'ı kopyalayın

### 3. Kurulum

1. İndirdiğiniz klasöre girin
2. **setup.bat** dosyasına çift tıklayın
3. Kurulum otomatik olarak tamamlanacak (5-10 dakika)
4. **.env** dosyasını not defteri ile açın
5. `BOT_TOKEN=` yazan yerin sonuna token'ınızı yapıştırın
6. Dosyayı kaydedin

### 4. Çalıştırma

1. **run.bat** dosyasına çift tıklayın
2. Bot başlayacak ve çalışır durumda kalacak
3. Telegram'da botunuza `/start` yazın

## Kullanım

### Temel Komutlar

- `/start` - Bot hakkında bilgi
- `/help` - Detaylı yardım
- `/add Dragon Rheum 6000` - Yeni fiyat alarmı ekle
- `/list` - Aktif alarmlarını görüntüle
- `/test Kuta` - Item fiyatını manuel kontrol et
- `/checknow` - Tüm alarmları zorla kontrol et

### Hızlı Alarm Ekleme

Mesaj olarak gönderin: `Dragon Rheum | 6000`

### Alarm Yönetimi

- `/list` komutu ile alarmlarınızı görün
- Her alarmın yanında "Şimdi Kontrol" ve "Sil" butonları var
- Maksimum 15 alarm ekleyebilirsiniz

## Sorun Giderme

### Bot çalışmıyor

1. İnternet bağlantınızı kontrol edin
2. .env dosyasında BOT_TOKEN doğru mu kontrol edin
3. run.bat'ı yeniden çalıştırın

### Python kurulum hatası

1. Windows güncellemelerinizi yapın
2. Antivirus'ü geçici olarak kapatın
3. [Python.org](https://python.org)'dan manuel indirin

### Captcha çıkıyor

1. `/test ItemAdı` komutu ile manuel çözün
2. Açılan tarayıcıda captcha'yı tamamlayın
3. Bot otomatik olarak devam edecek

### Antivirus uyarısı

Batch dosyaları bazen virüs olarak algılanabilir. Güvenlik programınızda exception ekleyin.

## Teknik Detaylar

### Sistem Gereksinimleri

- Windows 10/11
- 2 GB RAM
- 1 GB disk alanı
- İnternet bağlantısı

### Güncelleme

`update.bat` dosyasını çalıştırın veya yeni ZIP indirip üzerine çıkarın.

### Kaldırma

Klasörü silmeniz yeterli. Sisteminizde kalıcı değişiklik yapmaz.

## Lisans

Bu proje MIT lisansı altında dağıtılmaktadır.

## Destek

Sorun yaşarsanız:

1. Bu dokümandaki sorun giderme bölümünü kontrol edin
2. GitHub Issues'da yeni konu açın
3. Hata mesajlarını tam olarak kopyalayın

---

**Not:** Bu bot TTC'nin resmi bir ürünü değildir. Site kurallarına uygun şekilde çalışır.
