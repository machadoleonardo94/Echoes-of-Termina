#if !defined(SETUP_SDCARD)
#define SETUP_SDCARD

#include "shared/dependencies.h"
#include "components/SDCARD/features/sdcardFunctions.h"

bool setup_SDCARD();
void updatefromSD();
void progresscallback(size_t currSize, size_t totalSize);

bool setup_SDCARD()
{

    Serial.println("[SD CARD] SETUP STARTED!");
    sdcard = false;

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

void updatefromSD()
{
    Serial.printf("Current firmware build: %d\n", FIRMWARE_BUILD);
    Serial.printf("Searching for firmware update..\n");
    if (SD_MMC.remove("/firmware.bak"))
        Serial.printf("Old bak file removed..\n");
    File firmware = SD_MMC.open("/firmware.bin", FILE_READ);
    if (firmware)
    {
        Serial.println("Try to update!");
        Update.onProgress(progresscallback);
        Update.begin(firmware.size(), U_FLASH);
        Update.writeStream(firmware);
        if (Update.end())
        {
            Serial.println("Update finished!");
        }
        else
        {
            Serial.println("Update error!");
            Serial.println(Update.getError());
        }
        firmware.close();
        if (SD_MMC.rename("/firmware.bin", "/firmware.bak"))
            Serial.println("Firmware renamed successfully!");
        else
            Serial.println("Firmware rename error!");
        ESP.restart();
    }
}

// Update progress status
void progresscallback(size_t currSize, size_t totalSize)
{
    // Serial.printf("Update process at %d of %d bytes...\n", currSize, totalSize);
    int percent_upload = 100 * currSize / totalSize;
    Serial.printf("Update process at %d%%\n", percent_upload);
}
#endif // SETUP_SDCARD
