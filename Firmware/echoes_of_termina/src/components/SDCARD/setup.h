#if !defined(SETUP_SDCARD)
#define SETUP_SDCARD

#include "shared/dependencies.h"
#include "components/SDCARD/features/sdcardFunctions.h"
#include "components/SDCARD/features/sd_benchmark.h"

bool setup_SDCARD();
void updatefromSD();
void progresscallback(size_t currSize, size_t totalSize);

bool setup_SDCARD()
{

    Serial.println("[SD CARD] SETUP STARTED!");
    sdcard = false;

    // 1-bit SDMMC on both boards: Echoes V1 wires only D0, Marajoara_V3 wires
    // D0+D1 and no D2/D3 — so 4-bit was never available. 1-bit at 20 MHz is
    // ~2.5 MB/s, roughly two orders of magnitude above what FLAC playback plus
    // streamed lightshows need.
    // SD_MMC.setPins(SDMMC_CLK, SDMMC_CMD, SDMMC_D0, SDMMC_D1, SDMMC_D2, SDMMC_D3);
    SD_MMC.setPins(SDMMC_CLK, SDMMC_CMD, SDMMC_D0);
    if (!SD_MMC.begin("/sdcard", true))
    {
        Serial.println("Card Mount Failed");
        return false;
    }
    uint8_t cardType = SD_MMC.cardType();

    if (cardType == CARD_NONE)
    {
        Serial.println("No readable SD card attached");
        return false;
    }

    uint64_t cardSize = SD_MMC.cardSize() / (1024 * 1024);
    Serial.printf("SD Card Size: %lluMB\n", cardSize);

    // Dedicated top-level folders for the media library. Idempotent: only
    // create when missing so the log stays clean on subsequent boots.
    if (!SD_MMC.exists("/Echoes"))
        createDir(SD_MMC, "/Echoes");
    if (!SD_MMC.exists("/Echoes/Music"))
        createDir(SD_MMC, "/Echoes/Music");
    if (!SD_MMC.exists("/Echoes/Lightshows"))
        createDir(SD_MMC, "/Echoes/Lightshows");

    sdcard = true;
    Serial.println("[SD CARD] SETUP FINISHED!");
    return true;
}

// Flashes /firmware.bin from the SD root into the inactive OTA slot and reboots
// into it. setup() only calls this when that file exists, so the ordinary boot
// path never touches flash.
//
// This is the update route that works with the radio OFF. A board playing a
// standalone show cannot be reached by espota at all — wifi_disable_for_show()
// leaves WiFi.mode(WIFI_OFF) — so the image is staged over the portal first
// (POST /api/stage-firmware), and applied here on the next boot. The risky part
// then runs before WiFi, I2S or the decoder have claimed any RAM.
//
// The target slot comes from the partition table (ota_0/ota_1, 0x3F0000 each on
// partitions_8mb.csv). Safety: /firmware.bin is renamed whatever the outcome, so
// a bad image cannot put the board into a reflash-and-reboot loop. Success ->
// /firmware.bak (the previous backup is dropped at the start of the next
// attempt); failure -> /firmware.bad, which this function ignores and which can
// be pulled off the card to inspect.
void updatefromSD()
{
    Serial.printf("[SD-UPDATE] Current firmware build: %d\n", FIRMWARE_BUILD);

    File firmware = SD_MMC.open("/firmware.bin", FILE_READ);
    if (!firmware)
    {
        Serial.println("[SD-UPDATE] No /firmware.bin on the SD card");
        return;
    }

    size_t size = firmware.size();
    Serial.printf("[SD-UPDATE] Found /firmware.bin: %u bytes\n", (unsigned)size);
    if (size == 0)
    {
        firmware.close();
        Serial.println("[SD-UPDATE] Empty image — ignoring");
        SD_MMC.rename("/firmware.bin", "/firmware.bad");
        return;
    }

    // Keep exactly one generation of backup.
    if (SD_MMC.remove("/firmware.bak"))
        Serial.println("[SD-UPDATE] Old /firmware.bak removed");

    if (!Update.begin(size, U_FLASH))
    {
        // Usual causes: the image is bigger than the target OTA slot, or the
        // partition table has no inactive slot to write.
        Serial.printf("[SD-UPDATE] Update.begin failed: %s\n", Update.errorString());
        firmware.close();
        SD_MMC.rename("/firmware.bin", "/firmware.bad");
        Serial.println("[SD-UPDATE] Left as /firmware.bad — continuing to boot the current image");
        return;
    }

    Update.onProgress(progresscallback);
    size_t written = Update.writeStream(firmware);
    firmware.close();
    Serial.printf("[SD-UPDATE] Wrote %u of %u bytes\n", (unsigned)written, (unsigned)size);

    if (!Update.end())
    {
        Serial.printf("[SD-UPDATE] Update.end failed: %s\n", Update.errorString());
        SD_MMC.rename("/firmware.bin", "/firmware.bad");
        Serial.println("[SD-UPDATE] Left as /firmware.bad — NOT rebooting");
        return;
    }

    SD_MMC.rename("/firmware.bin", "/firmware.bak");
    Serial.println("[SD-UPDATE] Update finished — rebooting into the new image");
    delay(100); // let the serial lines above drain before the reset
    ESP.restart();
}

// Update.onProgress fires per chunk, which would be hundreds of lines for a 2 MB
// image. Print one line per 10% instead.
void progresscallback(size_t currSize, size_t totalSize)
{
    static int last_bucket = -1;
    if (totalSize == 0)
        return;
    int bucket = (int)(100 * currSize / totalSize) / 10;
    if (bucket != last_bucket)
    {
        last_bucket = bucket;
        Serial.printf("[SD-UPDATE] %d%%\n", bucket * 10);
    }
}
#endif // SETUP_SDCARD
