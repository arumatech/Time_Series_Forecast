sap.ui.define([
  "sap/ui/core/mvc/ControllerExtension",
  "sap/ui/core/Fragment",
  "sap/m/MessageToast"
], function (ControllerExtension, Fragment, MessageToast) {
  "use strict";

  return ControllerExtension.extend(
    "flatfile.ext.controller.ListReportExt",
    {
      onUploadCSV: async function () {
        if (!this.oDialog) {
          this.oDialog = await Fragment.load({
            name: "flatfile.ext.view.UploadDialog",
            controller: this
          });

          this.base.getView().addDependent(this.oDialog);
        }

        this.oDialog.open();
      },

      onCloseDialog: function () {
        if (this.oDialog) {
          this.oDialog.close();
        }
      },

      onFileChange: function (oEvent) {
        const aFiles = oEvent.getParameter("files");
        this._file = aFiles && aFiles.length ? aFiles[0] : null;
      },

      onUpload: function () {
        if (!this._file) {
          MessageToast.show("Select a CSV file first");
          return;
        }

        const oReader = new FileReader();

        oReader.onload = async (oEvent) => {
          try {
            const sCsvText = oEvent.target.result.replace(/^\uFEFF/, "");
            const oModel = this.base.getView().getModel();
            const oAction = oModel.bindContext("/uploadCSV(...)");

            oAction.setParameter("csvText", sCsvText);
            oAction.setParameter("fileName", this._file.name);

            await oAction.execute();

            MessageToast.show("Upload successful");

            oModel.refresh();
            this._file = null;
            this.onCloseDialog();
          } catch (oError) {
            console.error("CSV upload failed", oError);

            const sMessage =
              oError.message ||
              oError.error?.message ||
              "Unknown backend error";

            MessageToast.show("Upload failed: " + sMessage);
          }
        };

        oReader.onerror = () => {
          const sReason = oReader.error
            ? `${oReader.error.name}: ${oReader.error.message}`
            : "Unknown file-reading error";

          console.error("Unable to read CSV:", sReason);
          MessageToast.show("Unable to read file: " + sReason);
        };

        oReader.readAsText(this._file);
      }
    }
  );
});