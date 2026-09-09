sap.ui.define([
  "sap/ui/core/mvc/ControllerExtension",
  "sap/ui/core/Fragment",
  "sap/ui/model/json/JSONModel",
  "sap/ui/model/Filter",
  "sap/ui/model/FilterOperator",
  "sap/m/MessageToast"
], function (
  ControllerExtension,
  Fragment,
  JSONModel,
  Filter,
  FilterOperator,
  MessageToast
) {
  "use strict";

  return ControllerExtension.extend(
    "jobsumm.ext.controller.ListReportExt",
    {
      _getJobFormModel: function () {
        if (!this.oJobFormModel) {
          this.oJobFormModel = new JSONModel({
            forecastMICs: [],
            forecastMIC: "",
            sourceMICs: [],
            sourceMIC: "",
            targetMICs: [],
            targetMIC: ""
          });

          this.base.getView().setModel(
            this.oJobFormModel,
            "jobForm"
          );
        }

        return this.oJobFormModel;
      },

      onNewJob: async function () {
        this._getJobFormModel();

        if (!this.oNewJobDialog) {
          this.oNewJobDialog = await Fragment.load({
            id: this.base.getView().getId(),
            name: "jobsumm.ext.view.NewJobDialog",
            controller: this
          });

          this.base.getView().addDependent(
            this.oNewJobDialog
          );
        }

        this.oNewJobDialog.open();
      },

      onCloseNewJob: function () {
        this.oNewJobDialog?.close();
      },

      onContinue: async function () {
        const oJobTypeGroup =
          this.base.getView().byId("jobTypeGroup");

        const iSelectedIndex =
          oJobTypeGroup.getSelectedIndex();

        this.onCloseNewJob();

        if (iSelectedIndex === 0) {
          await this._openForecastDialog();
        } else {
          await this._openCorrelationDialog();
        }
      },

      _openForecastDialog: async function () {
        if (!this.oForecastDialog) {
          this.oForecastDialog = await Fragment.load({
            id: this.base.getView().getId(),
            name: "jobsumm.ext.view.ForecastDialog",
            controller: this
          });

          this.base.getView().addDependent(
            this.oForecastDialog
          );
        }

        this.oForecastDialog.open();
      },

      onCloseForecast: function () {
        this.oForecastDialog?.close();
      },

      _openCorrelationDialog: async function () {
        if (!this.oCorrelationDialog) {
          this.oCorrelationDialog = await Fragment.load({
            id: this.base.getView().getId(),
            name: "jobsumm.ext.view.CorrelationDialog",
            controller: this
          });

          this.base.getView().addDependent(
            this.oCorrelationDialog
          );
        }

        this.oCorrelationDialog.open();
      },

      onCloseCorrelation: function () {
        this.oCorrelationDialog?.close();
      },

      _openDCSIDValueHelp: async function (sTarget) {
        this._dcsidTarget = sTarget;

        if (!this.oInstrumentValueHelp) {
          this.oInstrumentValueHelp = await Fragment.load({
            id: this.base.getView().getId(),
            name: "jobsumm.ext.view.InstrumentValueHelp",
            controller: this
          });

          this.base.getView().addDependent(
            this.oInstrumentValueHelp
          );
        }

        this.oInstrumentValueHelp.open();
      },

      onForecastDCSIDValueHelp: function () {
        this._openDCSIDValueHelp("forecast");
      },

      onCorrelationSourceDCSIDValueHelp: function () {
        this._openDCSIDValueHelp("source");
      },

      onCorrelationTargetDCSIDValueHelp: function () {
        this._openDCSIDValueHelp("target");
      },

      onDCSIDSearch: function (oEvent) {
        const sSearchValue =
          oEvent.getParameter("value").trim();

        const oItemsBinding =
          oEvent.getSource().getBinding("items");

        oItemsBinding.changeParameters({
          $search: undefined
        });

        oItemsBinding.filter(
          sSearchValue
            ? [
                new Filter(
                  "DCSID",
                  FilterOperator.Contains,
                  sSearchValue
                )
              ]
            : []
        );
      },

      onDCSIDConfirm: async function (oEvent) {
        const oSelectedItem =
          oEvent.getParameter("selectedItem");

        if (!oSelectedItem) {
          return;
        }

        const sDCSID =
          oSelectedItem
            .getBindingContext()
            .getProperty("DCSID");

        switch (this._dcsidTarget) {
          case "forecast":
            this.base.getView()
              .byId("forecastDCSID")
              .setValue(sDCSID);
            break;

          case "source":
            this.base.getView()
              .byId("correlationSourceDCSID")
              .setValue(sDCSID);
            break;

          case "target":
            this.base.getView()
              .byId("correlationTargetDCSID")
              .setValue(sDCSID);
            break;
        }

        await this._loadMICs(
          sDCSID,
          this._dcsidTarget
        );

        this._clearDCSIDSearch();
      },

      onDCSIDCancel: function () {
        this._clearDCSIDSearch();
      },

      _clearDCSIDSearch: function () {
        const oItemsBinding =
          this.oInstrumentValueHelp
            ?.getBinding("items");

        if (oItemsBinding) {
          oItemsBinding.changeParameters({
            $search: undefined
          });

          oItemsBinding.filter([]);
        }
      },

      _loadMICs: async function (sDCSID, sTarget) {
        const oMainModel =
          this.base.getView().getModel();

        const oListBinding = oMainModel.bindList(
          "/INSTRUMENTS",
          null,
          null,
          [
            new Filter(
              "DCSID",
              FilterOperator.EQ,
              sDCSID
            )
          ],
          {
            $select: "MIC"
          }
        );

        try {
          const aContexts =
            await oListBinding.requestContexts(0, 1000);

          const aMICs = aContexts.map((oContext) => ({
            MIC: oContext.getProperty("MIC")
          }));

          const oJobFormModel =
            this._getJobFormModel();

          switch (sTarget) {
            case "forecast":
              oJobFormModel.setProperty(
                "/forecastMICs",
                aMICs
              );
              oJobFormModel.setProperty(
                "/forecastMIC",
                aMICs[0]?.MIC || ""
              );
              break;

            case "source":
              oJobFormModel.setProperty(
                "/sourceMICs",
                aMICs
              );
              oJobFormModel.setProperty(
                "/sourceMIC",
                aMICs[0]?.MIC || ""
              );
              break;

            case "target":
              oJobFormModel.setProperty(
                "/targetMICs",
                aMICs
              );
              oJobFormModel.setProperty(
                "/targetMIC",
                aMICs[0]?.MIC || ""
              );
              break;
          }
        } catch (oError) {
          console.error(
            "Unable to load MIC values",
            oError
          );

          MessageToast.show(
            "Unable to load MIC values"
          );
        }
      },

      _createJob: async function (oJobData, oDialog) {
        const oModel =
          this.base.getView().getModel();

        const oListBinding =
          oModel.bindList("/JOBSUMM");

        try {
          const oContext =
            oListBinding.create(oJobData);

          await oContext.created();

          MessageToast.show(
            `Job ${oContext.getProperty("JOB_ID")} created`
          );

          oDialog.close();
          oModel.refresh();
        } catch (oError) {
          console.error(
            "Unable to create job",
            oError
          );

          MessageToast.show(
            oError.message || "Unable to create job"
          );
        }
      },

      onExecuteForecast: async function () {
        const oView = this.base.getView();

        const sDCSID =
          oView.byId("forecastDCSID")
            .getValue()
            .trim();

        const sMIC =
          oView.byId("forecastMIC")
            .getSelectedKey();

        const sModel =
          oView.byId("forecastModel")
            .getSelectedKey();

        const sJobStartDate =
          oView.byId("forecastJobStartDate")
            .getValue();

        const sHorizonType =
          oView.byId("forecastHorizonType")
            .getSelectedKey();

        const iHorizon = Number(
          oView.byId("forecastHorizon").getValue()
        );

        if (
          !sDCSID ||
          !sMIC ||
          !sJobStartDate ||
          !Number.isInteger(iHorizon) ||
          iHorizon < 1
        ) {
          MessageToast.show(
            "Select a DCSID, MIC, and Job Start Date, and enter a positive integer horizon"
          );
          return;
        }

        await this._createJob(
          {
            FORCORR: "For",
            MODEL: sModel,
            SRCDCSID: null,
            SRCMIC: null,
            TARDCSID: sDCSID,
            TARMIC: sMIC,
            JOBSTARTDATE: sJobStartDate,
            HORTY: sHorizonType,
            HORVAL: iHorizon,
            JOBSTTMSTMP: null,
            JOBENDTMSTMP: null,
            JOBCOMPPCT: 0
          },
          this.oForecastDialog
        );
      },

      onExecuteCorrelation: async function () {
        const oView = this.base.getView();

        const sSourceDCSID =
          oView.byId("correlationSourceDCSID")
            .getValue()
            .trim();

        const sSourceMIC =
          oView.byId("correlationSourceMIC")
            .getSelectedKey();

        const sTargetDCSID =
          oView.byId("correlationTargetDCSID")
            .getValue()
            .trim();

        const sTargetMIC =
          oView.byId("correlationTargetMIC")
            .getSelectedKey();

        const sModel =
          oView.byId("correlationModel")
            .getSelectedKey();

        const sJobStartDate =
          oView.byId("correlationJobStartDate")
            .getValue();

        const sHorizonType =
          oView.byId("correlationHorizonType")
            .getSelectedKey();

        const iHorizon = Number(
          oView.byId("correlationHorizon")
            .getValue()
        );

        if (
          !sSourceDCSID ||
          !sSourceMIC ||
          !sTargetDCSID ||
          !sTargetMIC ||
          !sJobStartDate ||
          !Number.isInteger(iHorizon) ||
          iHorizon < 1
        ) {
          MessageToast.show(
            "Select source and target values, a Job Start Date, and enter a positive integer horizon"
          );
          return;
        }

        await this._createJob(
          {
            FORCORR: "Corr",
            MODEL: sModel,
            SRCDCSID: sSourceDCSID,
            SRCMIC: sSourceMIC,
            TARDCSID: sTargetDCSID,
            TARMIC: sTargetMIC,
            JOBSTARTDATE: sJobStartDate,
            HORTY: sHorizonType,
            HORVAL: iHorizon,
            JOBSTTMSTMP: null,
            JOBENDTMSTMP: null,
            JOBCOMPPCT: 0
          },
          this.oCorrelationDialog
        );
      }
    }
  );
});