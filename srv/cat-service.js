const cds = require("@sap/cds");

module.exports = cds.service.impl(function () {
  const { FLATFILE } = cds.entities("ZRISK");
  const { UPSERT } = cds.ql;

  this.before("CREATE", "JOBSUMM", async (req) => {
    if (!req.data.JOBSTARTDATE) {
      return req.reject(
        400,
        "Job Start Date is required"
      );
    }

    // Populated later when the job actually starts running.
    req.data.JOBSTTMSTMP = null;

    // Every newly created job begins in the entered state.
    req.data.JOBSTATUS = "ENTERED";

    const tx = cds.tx(req);

    const [result] = await tx.run(
      'SELECT "JOBSUMM_ID_V2".NEXTVAL AS "JOB_ID" FROM DUMMY'
    );

    req.data.JOB_ID = result.JOB_ID;
  });

  this.on("uploadCSV", async (req) => {
    const { csvText, fileName } = req.data;

    if (!csvText || !csvText.trim()) {
      return req.reject(400, "The CSV file is empty");
    }

    try {
      const sCleanCsv = csvText.replace(/^\uFEFF/, "");
      const aCsvRows = cds.parse.csv(sCleanCsv);

      if (aCsvRows.length < 2) {
        return req.reject(
          400,
          "The CSV contains no data rows"
        );
      }

      const aHeaders = aCsvRows[0].map((sHeader) =>
        String(sHeader).trim().toUpperCase()
      );

      const aDataRows = aCsvRows.slice(1);

      const aEntries = aDataRows.map((aRow, iIndex) => {
        const oRow = {};

        aHeaders.forEach((sHeader, iColumn) => {
          const vValue = aRow[iColumn];

          oRow[sHeader] =
            typeof vValue === "string"
              ? vValue.trim()
              : vValue;
        });

        const oEntry = {
          DCSID: oRow.DCSID,
          MIC: oRow.MIC,
          PRICETYPE: oRow.PRICETYPE,
          MKEYDT: convertMaturityDate(oRow.MKEYDT),
          PRICEDATE: convertPriceDate(oRow.PRICEDATE),
          PRICE: oRow.PRICE,
          CURRENCY: oRow.CURRENCY,
          PER: oRow.PER ? Number(oRow.PER) : null,
          UOM: oRow.UOM,
          FILENAME: fileName
        };

        const aRequiredFields = [
          "DCSID",
          "MIC",
          "PRICETYPE",
          "MKEYDT",
          "PRICEDATE"
        ];

        for (const sField of aRequiredFields) {
          if (!oEntry[sField]) {
            return req.reject(
              400,
              `CSV row ${iIndex + 2} is missing or has invalid ${sField}`
            );
          }
        }

        if (oEntry.MIC.length > 4) {
          return req.reject(
            400,
            `CSV row ${iIndex + 2}: MIC exceeds four characters`
          );
        }

        return oEntry;
      });

      await cds.tx(req).run(
        UPSERT.into(FLATFILE).entries(aEntries)
      );

      return aEntries.length;
    } catch (oError) {
      console.error("CSV upload error:", oError);

      if (
        oError.statusCode === 400 ||
        oError.code === 400
      ) {
        throw oError;
      }

      return req.reject(
        500,
        oError.message || "CSV upload failed"
      );
    }
  });
});

function convertMaturityDate(sValue) {
  if (!sValue) {
    return null;
  }

  const aParts = String(sValue).trim().split("/");

  if (aParts.length !== 3) {
    return null;
  }

  const [sDay, sMonth, sYear] = aParts;

  return [
    sYear.padStart(4, "20"),
    sMonth.padStart(2, "0"),
    sDay.padStart(2, "0")
  ].join("-");
}

function convertPriceDate(sValue) {
  if (!sValue) {
    return null;
  }

  const aParts = String(sValue).trim().split("/");

  if (aParts.length !== 3) {
    return null;
  }

  const [sMonth, sDay, sYear] = aParts;

  const sFullYear =
    sYear.length === 2 ? `20${sYear}` : sYear;

  return [
    sFullYear,
    sMonth.padStart(2, "0"),
    sDay.padStart(2, "0")
  ].join("-");
}