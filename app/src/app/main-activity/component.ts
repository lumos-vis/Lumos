// libraries
import * as d3 from "d3";
import $ from "jquery";
import "bootstrap";
import { Component, OnInit, AfterViewInit } from "@angular/core";
import { Router } from "@angular/router";
import { DomSanitizer } from "@angular/platform-browser";
import { ActivatedRoute } from "@angular/router";
import { OverlayScrollbarsComponent } from "overlayscrollbars-ngx";
// local
import { SessionPage, AppConfig, InteractionTypes, UserConfig } from "../models/config";
import { ChatService } from "../services/socket.service";
import { UtilsService } from "../services/utils.service";
import { ScatterPlot } from "../visualizations/main/scatter-plot-component";
import { StripPlot } from "../visualizations/main/strip-plot-component";
import { DotPlot } from "../visualizations/main/dot-plot-component";
import { BarChart } from "../visualizations/main/bar-chart-component";
import { LineChart } from "../visualizations/main/line-chart-component";
import { AttributeDistributionPlotConfig } from "../visualizations/awareness/component";
import { PriorBeliefStore } from "../store/prior-belief.store";
window.addEventListener("beforeunload", function (e) {
  // Cancel the event
  e.preventDefault(); // If you prevent default behavior in Mozilla Firefox prompt will always be shown
  // Chrome requires returnValue to be set
  e.returnValue = "";
});

// This is loaded as an external script not using npm, hence this step.
declare var vegaEmbed: any;

@Component({
  selector: "main-activity",
  templateUrl: "./component.html",
  providers: [ChatService],
  styleUrls: ["./component.scss"],
})
export class MainActivityComponent implements OnInit, AfterViewInit {
  objectKeys: { (o: object): string[]; (o: {}): string[] };
  objectValues: any;
  math: Math;
  userConfig: any;
  appConfig: any;
  currentPlotType: string;
  currentPlotInstance: string;
  scatterPlotInstance: ScatterPlot;
  stripPlotInstance: StripPlot;
  dotPlotInstance: DotPlot;
  barChartInstance: BarChart;
  lineChartInstance: LineChart;
  qFilterSliderConfig: any;
  taskConfigVis: Object;
  plotWidth: number;
  plotHeight: number;
  plotGroup: any;
  showPriorModal = false;
  private _lastLoggedChartType: string | null = undefined;

  constructor(
    private route: ActivatedRoute,
    public utilsService: UtilsService,
    public chatService: ChatService,
    private router: Router,
    public global: SessionPage,
    private sanitizer: DomSanitizer,
    private priorStore: PriorBeliefStore

  ) {
    this.objectKeys = Object.keys; // to help iterate over objects with *ngFor
    this.objectValues = Object.values; // to help iterate over objects with *ngFor
    this.math = Math; // to help iterate over objects with *ngFor
    this.userConfig = UserConfig; // access all user settings here
    this.appConfig = initializeAppModes(AppConfig); // access all app settings here
    this.currentPlotType = null; // default plot type
    this.currentPlotInstance = null; // default plot instance
    this.scatterPlotInstance = createPlotInstance(this, ScatterPlot);
    this.stripPlotInstance = createPlotInstance(this, StripPlot);
    this.dotPlotInstance = createPlotInstance(this, DotPlot);
    this.barChartInstance = createPlotInstance(this, BarChart);
    this.lineChartInstance = createPlotInstance(this, LineChart);
    this.route.queryParams.subscribe((params) => {
      if("level" in params){
        this.global.appLevel = params["level"];
      }
      const validTypes = ["CONTROL", "AWARENESS", "ADMIN"];
      if ("type" in params && validTypes.includes(params["type"])) {
        this.global.appType = params["type"];
      }
    });
    this.qFilterSliderConfig = (attribute) => {
      let attrConfig = this.appConfig[this.global.appMode]["attributes"][attribute];
      let formatter = { from: Number, to: this.utilsService.formatLargeNum };
      return {
        step: attrConfig["step"],
        range: {
          min: attrConfig["min"],
          max: attrConfig["max"],
        },
        pips: { mode: "count", values: 3, density: 10, format: formatter },
        tooltips: [formatter, formatter],
        format: { from: Number, to: Number },
      };
    };
    this.taskConfigVis = {};
    this.plotWidth = null;
    this.plotHeight = null;
    this.plotGroup = null;
  }

  /** ====================== PRIOR ELICITATION METHODS ====================== */
  getColumnValues(): Record<string, number[]> {
    const data = this.userConfig["originalDataset"]; // the loaded CSV rows
    if (!data) return {}; // data not loaded yet
    const qAttrs = this.appConfig[this.global.appMode]?.attributeDatatypeList?.Q || []; // only quantitative attributes
    const result: Record<string, number[]> = {}; // dictionary of column name to array of values for that column
    qAttrs.forEach(attr => {
      result[attr] = data.map(row => row[attr]).filter(v => v != null); // extract all values for this column, drop nulls
    });
    return result;
  }

  getCategoricalValues(): Record<string, string[]> {
    const data = this.userConfig["originalDataset"];
    if (!data) return {};
    const dtl = this.appConfig[this.global.appMode]?.attributeDatatypeList;
    if (!dtl) return {};
    const catAttrs: string[] = (dtl['N'] || []).concat(dtl['O'] || []);
    const result: Record<string, string[]> = {};
    catAttrs.forEach(attr => {
      const seen = new Set<string>();
      data.forEach(row => { if (row[attr] != null) seen.add(String(row[attr])); });
      result[attr] = Array.from(seen).sort();
    });
    return result;
  }

  /** ====================== INITIALIZATION METHODS ======================= */

  /**
   * Required for ng.
   */
  ngOnInit(): void {
    switch (this.global.appLevel) {
      case "practice":
        this.global.appMode = "cars.csv";
        break;
      case "live":
        this.global.appMode = "credit_risk.csv";
        break;
    }
    switch (this.global.appType) {
      case "CONTROL":
        this.userConfig["awarenessVisLayers"] = ["Target"];
        break;
      case "AWARENESS":
        this.userConfig["awarenessVisLayers"] = ["Target", "Focus", "Selection"];
        break;
      case "ADMIN":
        this.userConfig["awarenessVisLayers"] = ["Target", "Focus", "Selection"];
        break;
    }
    this.initLumos();
  }

  /**
   * Required for ng.
   */
  ngAfterViewInit(): void {
    this.setWidthsForAwarenessPanelVis();
  }

  /**
   * Initialize application variables, load the dataset, initialize the
   * attributes with a default "filterModel", and connect to the server.
   */
  initLumos() {
    let context = this;
    let dataset = this.appConfig[this.global.appMode];

    // Maintain a separate list of Nominal (N), Ordinal (O), Temporal (T),
    // and Quantitative (Q) attributes for *ngFor purposes.
    dataset.attributeList = [];
    dataset.attributeDatatypeList = {
      N: [],
      O: [],
      T: [],
      Q: [],
    };

    // set the current plot type (in case data set has changed)
    context.currentPlotType = dataset["chartType"];

    // Single pass over all attributes in the dataset to initialize variables.
    dataset["orderedAttributeList"].forEach((attr) => {
      let attribute = dataset["attributes"][attr];
      // Create a ' ' (space)-free version of attributes
      attribute["cleaned"] = attr.replace(/ /g, "").toLowerCase();
      // Initialize all attributes of the Selected data point to "-"
      dataset["selectedObject"][attr] = "-";
      // Initialize all attribute Divergent Values to 0.
      dataset["attributeBiasValues"][attr] = 0;
      // Initialize attribute interacted with values to 0.
      dataset["attributeInteracted"][attr] = 0;
      // Initialize attribute interacted ratio to 0.
      dataset["ratioAttributeInteracted"][attr] = 0;
      // Add attribute to list
      dataset.attributeList.push(attr);
      // Update the list of Nominal (N), Ordinal (O), Temporal (T),
      // and Quantitative (Q) attributes defined above for *ngFor purposes.
      dataset.attributeDatatypeList[attribute["datatype"]].push(attr);
    });

    // Load the data itself from file
    const fp = "./assets/" + context.global.appMode;
    d3.csv(fp).then(function (data: Array<Object>) {
      // parse data type for type conversion
      // let parseFormat: any = d3.timeFormat("%Y");
      // let parseTime = d3.timeFormat(parseFormat); // expect YYYY
      data.forEach((d) => {
        Object.keys(d).forEach((attr) => {
          if (context.utilsService.isMeasure(dataset, attr, "Q")) {
            d[attr] = parseFloat(d[attr]);
          } else if (context.utilsService.isMeasure(dataset, attr, "T")) {
            // d[attr] = parseTime(d[attr]); // ARPIT TODO
          }
        });
      });

      // store copy of the original dataset
      context.userConfig["originalDataset"] = data;
      // Initialize the data set dictionary
      context.userConfig["originalDatasetDict"] = {};
      // get the id of the item from the dataset's primary Key attribute
      let pk = dataset["primaryKey"];

      // Update attribute lists with original data set
      context.userConfig["originalDataset"].forEach((d) => {
        // initialize data point unique keys
        if (!d.hasOwnProperty("color")) d["color"] = "white";
        if (!d.hasOwnProperty("selected")) d["selected"] = false;
        if (!d.hasOwnProperty("timesVisited")) d["timesVisited"] = 0;
        if (!d.hasOwnProperty("ratioTimesVisited")) d["ratioTimesVisited"] = 0;
        // Update the originalDatasetDict variable defined above.
        context.userConfig["originalDatasetDict"][d[pk]] = d;
        // Set the TaskModel/Domain for Nominal Attributes
        dataset.attributeDatatypeList["N"].forEach((attr) => {
          let attrConfig = dataset["attributes"][attr];
          let attrDomain = attrConfig["types"];
          if (attrDomain.indexOf(d[attr]) == -1) attrDomain.push(d[attr]);
          attrConfig["types"].forEach((type) => (attrConfig["taskModel"][type] = 1));
        });
        // Create Range for Temporal Attributes
        dataset.attributeDatatypeList["T"].forEach((attr) => {
          let attrConfig = dataset["attributes"][attr];
          d[attr] = parseFloat(d[attr]);
          attrConfig["max"] = Math.max(attrConfig["max"], d[attr]);
          attrConfig["min"] = Math.min(attrConfig["min"], d[attr]);
          // set Numerical Attributes.
          attrConfig["taskModel"] = [
            [attrConfig["min"], 0],
            [attrConfig["min"], 1],
            [attrConfig["max"], 1],
            [attrConfig["max"], 0],
          ];
        });
        // Create Range for Quantitative Attributes
        dataset.attributeDatatypeList["Q"].forEach((attr) => {
          let attrConfig = dataset["attributes"][attr];
          d[attr] = parseFloat(d[attr]);
          attrConfig["max"] = Math.max(attrConfig["max"], d[attr]);
          attrConfig["min"] = Math.min(attrConfig["min"], d[attr]);
          // set Numerical Attributes.
          attrConfig["taskModel"] = [
            [attrConfig["min"], 0],
            [attrConfig["min"], 1],
            [attrConfig["max"], 1],
            [attrConfig["max"], 0],
          ];
        });
      });

      // Reset filters by setting the initial "filterModel"
      //  (i.e., `[min, max]` for 'Q' and `types` for 'N' attributes)
      //  to the default domain and range.
      dataset.attributeList.forEach((attr) => {
        context.removeFilter(attr, false, false);
      });

      // if switching between datasets, re-initialize existing plot
      //  (otherwise this does nothing)
      initializePlotInstance(context, context.currentPlotType);
      context.updateVis();

      /** Connect to Server to Send/Receive Messages over WebSocket */
      context.chatService.removeAllListenersAndDisconnectFromSocket();

      context.chatService.connectToSocket();

      context.chatService.getConnectEventResponse().subscribe(() => {
        console.log("connected to socket");
      });

      context.chatService.getDisconnectEventResponse().subscribe(() => {
        console.log("disconnected from socket");
      });

      context.chatService.getInteractionResponse().subscribe((obj) => {
        let dataOut = obj["output_data"];
        if (dataOut != null) {
          let countObj = dataOut["data_point_distribution"][1]["counts"];
          // retrieve bias values
          dataset["attributeBiasValues"] = dataOut["attribute_distribution"][0];
          // update times visisted (computed server-side)
          Object.keys(countObj).forEach((id) => {
            let dataPoint = context.userConfig["originalDatasetDict"][id];
            dataPoint["timesVisited"] = countObj[id];
          });
          // calculate sum of all attribte bias values
          dataset["sumAttributeBiasValues"] = Object.values(dataset["attributeBiasValues"]).reduce(
            context.utilsService.sum,
            0
          ) as number;
          if (["dotplot", "barchart", "linechart"].indexOf(context.currentPlotInstance) !== -1) {
            // update point color for hovered Objects (only if visible!)
            let hoveredPointsList = Object.values(dataset["hoveredObjects"]["points"]);
            hoveredPointsList.forEach((dataPoint) =>
              context.utilsService.colorDataPoint(context, dataPoint, hoveredPointsList)
            );
          }
          // update attribute distributions
          let attrDist = dataset["attributeDistribution"];
          Object.keys(attrDist["original"]).forEach((attr) => {
            const attrIsN = context.utilsService.isMeasure(dataset, attr, "N");
            const attrIsO = context.utilsService.isMeasure(dataset, attr, "O");
            const attrIsT = context.utilsService.isMeasure(dataset, attr, "T");
            const attrIsQ = context.utilsService.isMeasure(dataset, attr, "Q");
            let attrConfig = dataOut["attribute_distribution"][1][attr];
            if (attrIsN || attrIsO) {
              attrDist["interacted"][attr] = attrConfig["interaction_distr_dict"];
              dataset["attributeCoverage"]["interacted"][attr] = [
                dataOut["attribute_coverage"][1][attr]["coverage"],
                dataOut["attribute_coverage"][1][attr]["quantiles"],
              ];
            } else if (attrIsT || attrIsQ) {
              attrDist["interacted"][attr] = attrConfig["interaction_distr"];
              dataset["attributeCoverage"]["interacted"][attr] = [
                dataOut["attribute_coverage"][1][attr]["coverage"],
                dataOut["attribute_coverage"][1][attr]["quantiles"],
              ];
            }
          });
          context.updateAwarenessPanel();
          context.updateVis();
        }
      });

      context.chatService.getAttributeDistribution().subscribe((obj) => {
        let attrDist = dataset["attributeDistribution"];
        let attrCov = dataset["attributeCoverage"];
        if (obj != null) {
          attrDist["original"] = obj[context.global.appMode];
          Object.keys(attrDist["original"]).forEach((attr) => {
            if (!attrDist["interacted"].hasOwnProperty(attr)) {
              attrDist["interacted"][attr] = [];
            }
            if (!attrCov["interacted"].hasOwnProperty(attr)) {
              attrCov["interacted"][attr] = [{}, []];
            }
          });
          context.updateAwarenessPanel();
          context.updateVis();
        }
      });

      context.showPriorModal = true;
    });
  }

  /** ========================= UPDATE METHODS ============================ */

  /**
   * Call this function to update the visualizations in the awareness panel.
   */
  updateAwarenessPanel(specific_attr = null) {
    let context = this;
    let dataset = this.appConfig[this.global.appMode];

    // Sizes of each awareness panel
    const width = this.userConfig["sizes"]["awarenessPanel"]["width"];
    const height = this.userConfig["sizes"]["awarenessPanel"]["height"];

    /* Attribute Distribution Plot - Start */
    dataset.attributeList.forEach((attr) => {
      // Only update the awareness visualizations:
      // 1) For the visible (expanded) attribute cards.
      // 2) If an explicit update is requested for a specific attribute.
      if (!specific_attr || (specific_attr && attr == specific_attr)) {
        if (
          dataset["attributes"][attr]["awarenessPanel"]["isExpanded"] ||
          dataset["attributes"][attr]["awarenessPanel"]["isBookmarked"]
        ) {
          // Initialize Data and Common Variables
          let data = [];
          let configObject = {};
          let selectedDataObj: any = [];
          let sumSelected = 0;
          let attrConfig = dataset["attributes"][attr];
          const attrIsN = context.utilsService.isMeasure(dataset, attr, "N");
          const attrIsO = context.utilsService.isMeasure(dataset, attr, "O");
          const attrIsT = context.utilsService.isMeasure(dataset, attr, "T");
          const attrIsQ = context.utilsService.isMeasure(dataset, attr, "Q");
          const attrShorthand = attrConfig["cleaned"];
          const taskType = attrConfig["taskType"];
          const originalDataObj = dataset["attributeDistribution"]["original"][attr];
          const interactedDataObj = dataset["attributeDistribution"]["interacted"][attr];
          const attrDistributionPlotContainerId = `#awarenessPlot-${attrShorthand}`;
          const sumOriginal = Object.values(originalDataObj).reduce(context.utilsService.sum, 0) as number;
          const sumInteracted = Object.values(interactedDataObj).reduce(context.utilsService.sum, 0) as number;

          /*  Vega Configure and Render Vis - Start */
          if (attrIsN || attrIsO) {
            // attribute is N/O => prepare categorical settings
            selectedDataObj = {};
            Object.keys(originalDataObj).forEach((key) => {
              selectedDataObj[key] = 0;
            });
            Object.values(dataset["selectedObjects"]).forEach((obj) => {
              selectedDataObj[obj[attr]] += 1;
            });
            sumSelected = Object.values(selectedDataObj).reduce(context.utilsService.sum, 0) as number;

            // Initialize ConfigObject
            configObject = JSON.parse(JSON.stringify(AttributeDistributionPlotConfig["N"]));

            // Depending on what the taskType is:
            //  Create/Update Data for layer #1 and #2
            //  for the Original and Interacted plots, respectively.

            /*  Render N/O Task Type - Start */
            switch (taskType) {
              case "proportional":
                switch (context.userConfig.awarenessMode) {
                  case "Raw":
                    Object.keys(originalDataObj).forEach((key) => {
                      data.push({
                        x: key,
                        count_data: originalDataObj[key],
                        count_focus: interactedDataObj[key],
                        count_selection: selectedDataObj[key],
                      });
                    });
                    break;
                  case "Percentage":
                    Object.keys(originalDataObj).forEach((key) => {
                      data.push({
                        x: key,
                        count_data: originalDataObj[key] / sumOriginal,
                        count_focus: interactedDataObj[key] / sumInteracted,
                        count_selection: selectedDataObj[key] / sumSelected,
                      });
                    });
                    break;
                }
                break;
            }
            /*  Render N/O Task Type - End */

            const size = (width * 0.8) / attrConfig["types"].length;
            // For the 1st layer (underlying data distribution),
            // set the bandSize parameter (width of the ticks).
            configObject["config"]["tick"]["bandSize"] = size;
            // For the 2nd and 3rd layers (bar chart of interactions and selections), set the width
            // explicitly
            configObject["layer"][1]["mark"]["size"] = size;
            configObject["layer"][2]["mark"]["size"] = size;
          } else if (attrIsQ || attrIsT) {
            // attribute is Q/T => prepare vis for numerical settings
            // Initialize common variables
            let quantiles = [];
            selectedDataObj = [];
            Object.values(dataset["selectedObjects"]).forEach((obj) => {
              selectedDataObj.push(obj[attr]);
            });

            /*  Render Q/T Task Type - Start */
            switch (taskType) {
              /** TASK CONFIGURATION IS SET TO PROPORTIONAL */
              case "proportional":
                // Determine which layer to show on top.
                let biggerArr;
                let smallerArr;
                let smallestArr = [];
                let biggerAttrType;
                Object.values(dataset["selectedObjects"]).forEach((value) => {
                  smallestArr.push(value[attr]);
                });
                if (originalDataObj.length > interactedDataObj.length) {
                  biggerArr = originalDataObj;
                  biggerAttrType = "Target";
                  smallerArr = interactedDataObj;
                } else {
                  smallerArr = originalDataObj;
                  biggerAttrType = "focus_field";
                  biggerArr = interactedDataObj;
                }
                biggerArr.forEach((item, counter) => {
                  if (biggerAttrType == "Target") {
                    data.push({
                      [attr]: parseFloat(item),
                      ["focus_field"]: counter < smallerArr.length ? parseFloat(smallerArr[counter]) : null,
                      ["selection_field"]: counter < smallestArr.length ? parseFloat(smallestArr[counter]) : null,
                    });
                  } else {
                    data.push({
                      [attr]: counter < smallerArr.length ? parseFloat(smallerArr[counter]) : null,
                      ["focus_field"]: parseFloat(item),
                      ["selection_field"]: counter < smallestArr.length ? parseFloat(smallestArr[counter]) : null,
                    });
                  }
                });
                // Instantiate the correct config Object
                switch (context.userConfig.awarenessMode) {
                  case "Raw":
                    configObject = JSON.parse(JSON.stringify(AttributeDistributionPlotConfig["Q-raw"]));
                    break;
                  case "Percentage":
                    configObject = JSON.parse(JSON.stringify(AttributeDistributionPlotConfig["Q-pct"]));
                    break;
                }
                // Set the Interpolate metric based on the selected mode.
                configObject["layer"][0]["mark"]["interpolate"] = context.userConfig.interpolateMode;
                configObject["layer"][1]["mark"]["interpolate"] = context.userConfig.interpolateMode;
                configObject["layer"][2]["mark"]["interpolate"] = context.userConfig.interpolateMode;
                // Set the Filter transform to remove NULLs
                configObject["layer"][0]["transform"][0]["filter"] = "datum['" + attr + "'] !== null";
                // Set the Field to BIN.
                configObject["layer"][0]["transform"][1]["field"] = attr;
                break;
            }
            /*  Render Q/T Task Type - End */

            if (quantiles.length > 0) {
              // For the 1st layer (strip plot of underlying data distribution),
              // set the bandSize parameter (width of the ticks).
              configObject["config"]["tick"]["bandSize"] = (width * 0.8) / quantiles.length;
              // For the 2nd and 3rd layers (bar chart of interactions and selections), set the width
              // explicitly
              configObject["layer"][1]["mark"]["size"] = (width * 0.8) / quantiles.length;
              configObject["layer"][2]["mark"]["size"] = (width * 0.8) / quantiles.length;
            }
          }
          /*  Vega Configure and Render Vis - End */

          // Set the Width and Height
          configObject["width"] = width;
          configObject["height"] = height;
          // Set the Data
          configObject["data"]["values"] = data;
          // Update Y Axis Title
          configObject = setYAxisTitle(context.userConfig.awarenessMode, configObject);

          // Determine which layers (e.g., selected + interacted) are to be shown.
          // Rule 1: It should be configured in the `awarenessVisLayers` array
          // Rule 2: If there are no user interactions or selections yet, remove those layers to save computation.
          let layerIndicesSet = new Set();
          this.userConfig["awarenessVisLayers"].forEach((layer) => {
            switch (layer) {
              case "Target":
                if (
                  Object.values(originalDataObj).length > 0 &&
                  ["CONTROL", "AWARENESS", "ADMIN"].indexOf(this.global.appType) !== -1
                ) {
                  layerIndicesSet.add(0);
                }
                break;
              case "Focus":
                if (
                  Object.values(interactedDataObj).length > 0 &&
                  ["AWARENESS", "ADMIN"].indexOf(this.global.appType) !== -1
                ) {
                  layerIndicesSet.add(1);
                }
                break;
              case "Selection":
                // For numerical attributes
                if (Array.isArray(selectedDataObj)) {
                  if (Object.values(selectedDataObj).length > 0 && ["ADMIN"].indexOf(this.global.appType) !== -1) {
                    layerIndicesSet.add(2);
                  }
                }
                // For categorical attributes
                else {
                  if (
                    Object.values(selectedDataObj).reduce(context.utilsService.sum) as number > 0 &&
                    ["ADMIN"].indexOf(this.global.appType) !== -1
                  ) {
                    layerIndicesSet.add(2);
                  }
                }
                break;
            }
          });

          // Delete the unwanted layers from the Vega-Lite layer specification.
          let layerIndicesArray = Array.from(layerIndicesSet);
          for (var i = 2; i >= 0; i--) {
            if (layerIndicesArray.indexOf(i) === -1) {
              configObject["layer"].splice(i, 1);
            }
          }

          let domain, range;
          switch (this.global.appType) {
            case "CONTROL":
              domain = ["Target"];
              range = ["black"];
              break;
            case "AWARENESS":
              domain = ["Focus", "Target"];
              range = ["#3498db", "black"];
              break;
            case "ADMIN":
              domain = ["Focus", "Target", "Selection"];
              range = ["#3498db", "black", "#2ecc71"];
              break;
            default:
              domain = ["Target"];
              range = ["black"];
              break;
          }

          for (var i = 0; i < configObject["layer"].length; i++) {
            configObject["layer"][i]["encoding"]["color"]["scale"]["domain"] = domain;
            configObject["layer"][i]["encoding"]["color"]["scale"]["range"] = range;
          }

          // Store reference to this vis object
          attrConfig["distributionPlotConfigObject"] = configObject;
          // Call the Render function.
          vegaEmbed(attrDistributionPlotContainerId, configObject, {
            actions: false,
          });
        }
      }
    });
    /* Attribute Distribution Plot - End */
  }

  /**
   * Call this function to update the current visualization depending on the
   * one that's chosen.
   */
  updateVis() {
    switch (this.currentPlotType) {
      case "scatterplot":
        // use VIS Matrix to determine which version to update
        let context = this;
        let dataset = context.appConfig[context.global.appMode];
        const xVar = dataset["xVar"];
        const yVar = dataset["yVar"];
        const xIsQ = context.utilsService.isMeasure(dataset, xVar, "Q");
        const yIsQ = context.utilsService.isMeasure(dataset, yVar, "Q");
        if (!(xVar || yVar) || xIsQ || yIsQ) {
          context.scatterPlotInstance.update();
        } else {
          context.dotPlotInstance.update();
        }
        break;
      case "stripplot":
        this.stripPlotInstance.update();
        break;
      case "barchart":
        this.barChartInstance.update();
        break;
      case "linechart":
        this.lineChartInstance.update();
        break;
      case null:
        $("#plot_container").empty(); // clear existing plot
        break;
      default:
        console.log(`Invalid plot type '${this.currentPlotType}'`);
        break;
    }
  }

  /** ======================== INTERFACE METHODS ========================== */

  prev(path, params) {
    this.chatService.removeAllListenersAndDisconnectFromSocket();
    this.router.navigateByUrl(path);
    this.router.navigate([path], { queryParams: params });
  }

  next(path, params) {
    this.chatService.sendMessageToSaveLogs();
    this.chatService.removeAllListenersAndDisconnectFromSocket();
    this.global["app-" + this.global.appLevel]["completed"] = true;
    this.global["app-" + this.global.appLevel]["timestamp"] = new Date().toLocaleString();
    this.router.navigate([path], { queryParams: params });
  }

  /**
   * Set CSS styling for attribute panel cards programmatically.
   */
  styleAttributePanelCard(attribute) {
    return {
      "background-repeat": "no-repeat",
      "background-image": this.getPanelCardBGImage(attribute, "attributes"),
      "background-size": this.getPanelCardBGSize(attribute, "attributes"),
      color: this.getPanelCardTxtColor(attribute, "attributes"),
    };
  }

  /**
   * Set CSS styling for awareness panel cards programmatically.
   */
  styleAwarenessPanelCard(attribute) {
    return {
      "background-repeat": "no-repeat",
      "background-image": this.getPanelCardBGImage(attribute, "awareness"),
      "background-size": this.getPanelCardBGSize(attribute, "awareness"),
    };
  }

  /**
   * Combines bin name and attribute for details table header.
   */
  getDetailsTableHeader() {
    let hoveredObjects = this.appConfig[this.global.appMode]["hoveredObjects"];
    return [hoveredObjects["binAttr"], hoveredObjects["binName"]].filter(Boolean).join(": ");
  }

  /**
   * Settings for `overlay-scrollbars` in details table view.
   */
  getDetailsTableScrollbarOptions() {
    return {
      className: "os-theme-dark deviant-scrollbars os-theme-dark-edgy offset-scrollbars",
      paddingAbsolute: true,
      sizeAutoCapable: true,
      scrollbars: {
        visibility: "auto",
        autoHide: "never",
        clickScrolling: true,
        dragScrolling: true,
      },
    };
  }

  /**
   * Gets size of color gradient across attribute/awareness panel bars.
   * Used to show/hide the color of the panel card header.
   */
  getPanelCardBGSize(attribute, panelType) {
    let dataset = this.appConfig[this.global.appMode];
    let size = "0% 100%, 100% 100%"; // default size
    switch (panelType) {
      case "awareness":
        size = dataset["sumAttributeBiasValues"] == 0 ? "0% 100%, 100% 100%" : "100% 100%, 0% 100%";
        break;
      case "attributes":
        size = dataset["attributeInteracted"][attribute] == 0 ? "0% 100%, 100% 100%" : "100% 100%, 0% 100%";
        break;
      default:
        size = "0% 100%, 100% 100%";
        break;
    }
    return size;
  }

  /**
   * Calculates color gradient of attribute/awareness panel bars.
   */
  getPanelCardBGImage(attribute, panelType) {
    if (this.global.appType === "CONTROL") return "none";
    let context = this;
    let dataset = context.appConfig[context.global.appMode];
    let color = "#FFFFFFF"; // default color set to white
    switch (panelType) {
      case "awareness":
        let biasValue = dataset["attributeBiasValues"][attribute];
        switch (context.userConfig.awarenessColorScale) {
          case "Divergent":
            color = context.userConfig.awarenessDivergentColorScale(biasValue);
            break;
          case "Sequential":
            color = context.userConfig.awarenessSequentialColorScale(biasValue);
            break;
          default:
            color = "#FFFFFFF";
            break;
        }
        break;
      case "attributes":
        // Get the correct color scale
        let colorScale;
        switch (context.userConfig.attributeColorScale) {
          case "Divergent":
            colorScale = context.userConfig.focusDivergentColorScale;
            break;
          case "Sequential":
            colorScale = context.userConfig.focusSequentialColorScale;
            break;
          default:
            colorScale = context.userConfig.focusDivergentColorScale;
            break;
        }
        // Calculate the ratio of interactions based on color by mode
        let attrInteractions = dataset["attributeInteracted"][attribute];
        let allInteractions = Object.values(dataset["attributeInteracted"]);
        switch (context.userConfig.attributeColorByMode) {
          case "abs":
            const sumInteracted = allInteractions.reduce(context.utilsService.sum, 0) as number;
            dataset["ratioAttributeInteracted"][attribute] = attrInteractions / sumInteracted;
            break;
          case "rel":
            const maxInteracted = allInteractions.reduce(context.utilsService.max, 0) as number;
            dataset["ratioAttributeInteracted"][attribute] = attrInteractions / maxInteracted;
            break;
          case "binary":
            const interacted = attrInteractions > 0;
            dataset["ratioAttributeInteracted"][attribute] = !interacted ? 0 : 1;
            break;
          default:
            dataset["ratioAttributeInteracted"][attribute] = 0;
            break;
        }
        // Get the color from the color scale
        color = colorScale(dataset["ratioAttributeInteracted"][attribute]);
        break;
      default:
        color = "#FFFFFFF";
        break;
    }
    return `linear-gradient(${color}, ${color}), linear-gradient(white, white)`;
  }

  /**
   * Gets FONT COLOR of the bars.
   */
  getPanelCardTxtColor(attribute, panelType) {
    if (this.global.appType === "CONTROL") return "black";
    let dataset = this.appConfig[this.global.appMode];
    let txtColor = "black"; // default text color
    if (panelType == "attributes" && this.userConfig.attributeColorScale == "Sequential") {
      // card background could be colored => set text to white if color is "too dark"
      txtColor = dataset["ratioAttributeInteracted"][attribute] > 0.7 ? "white" : "black";
    }
    return txtColor;
  }

  /**
   * Gets width of largest text item in options for select element.
   */
  getSelectWidth(setting, mapping = null) {
    let canvas = document.createElement("canvas");
    let ctx = canvas.getContext("2d");
    ctx.font = "normal normal 600 1rem/1.5 sans-serif";
    let txt = document.createElement("textarea");
    let getWidth; // convenience function for readability
    if (mapping) {
      getWidth = (val) => getTextWidth(ctx, decodeHtml(txt, this.userConfig[mapping][val]));
    } else {
      getWidth = (val) => getTextWidth(ctx, decodeHtml(txt, val));
    }
    let longest; // longest string from the list of strings
    if (Array.isArray(setting)) {
      // setting is a list of options
      longest = setting.reduce((acc, cur) => (getWidth(acc) > getWidth(cur) ? acc : cur));
    } else {
      // setting is a string for the options list in UserConfig
      longest = this.userConfig[setting].reduce((acc, cur) => (getWidth(acc) > getWidth(cur) ? acc : cur));
    }
    return `${getWidth(longest) + 50}px`;
  }

  /**
   * Update the size of various panels / views.
   */
  setWidthsForAwarenessPanelVis() {
    this.userConfig["sizes"]["awarenessPanel"]["width"] = $(".awareness-panel-body").width() - 100;
  }

  /**
   * Unset the `isBookmarked` state for all attributes.
   */
  resetAllBookmarks($event) {
    let dataset = this.appConfig[this.global.appMode];
    dataset.attributeList.forEach((attr) => {
      dataset["attributes"][attr]["awarenessPanel"]["isBookmarked"] = false;
    });
    this.collapseAccordion();

    /* Prepare and Send New Message - Start */
    let message = this.utilsService.initializeNewMessage(this);
    message.interactionType = InteractionTypes.TOGGLE_ALL_ATTRIBUTE_BOOKMARK_AWARENESS_PANEL;
    message.data = {
      isBookmarked: false,
      eventX: null,
      eventY: null,
    };
    this.chatService.sendInteractionResponse(message);
    /* Prepare and Send New Message - End */

    if ($event) $event.stopPropagation();
  }

  /**
   * Event Listener when SORT order in the Distribution Panel is changed.
   */
  onChangeDistributionPanelSort(model) {
    console.log(model);
    /* Prepare and Send New Message - Start */
    let message = this.utilsService.initializeNewMessage(this);
    message.interactionType = InteractionTypes.CHANGE_DISTRIBUTION_PANEL_SORT;
    message.data = {
      sortBy: model,
      eventX: null,
      eventY: null,
    };
    this.chatService.sendInteractionResponse(message);
    /* Prepare and Send New Message - End */
  }

  /**
   * Event Listener when SORT order in the Attribute Panel is changed.
   */
  onChangeAttributePanelSort(model) {
    /* Prepare and Send New Message - Start */
    let message = this.utilsService.initializeNewMessage(this);
    message.interactionType = InteractionTypes.CHANGE_ATTRIBUTE_PANEL_SORT;
    message.data = {
      sortBy: model,
      eventX: null,
      eventY: null,
    };
    this.chatService.sendInteractionResponse(message);
    /* Prepare and Send New Message - End */
  }

  /**
   * Toggle the Accordion of the Attribute Panel Settings
   */
  toggleAttributePanelSettingsAccordion() {
    $("#collapseAttributeControls").toggleClass("show");
  }

  /**
   * Toggle the Accordion of the Awareness Panel Settings
   */
  toggleAwarenessPanelSettingsAccordion() {
    $("#collapseAwarenessControls").toggleClass("show");
  }

  /**
   * Toggle the `isBookmarked` state for given attribute.
   */
  toggleBookmark(attribute, $event) {
    let context = this;
    let attrConfig = context.appConfig[context.global.appMode]["attributes"][attribute];
    attrConfig["awarenessPanel"]["isBookmarked"] = !attrConfig["awarenessPanel"]["isBookmarked"];
    if (attrConfig["awarenessPanel"]["isBookmarked"]) {
      setTimeout(function () {
        context.updateAwarenessPanel(attribute);
      });
    } else {
      this.collapseAccordion(attribute);
    }
    /* Prepare and Send New Message - Start */
    let message = this.utilsService.initializeNewMessage(this);
    message.interactionType = InteractionTypes.TOGGLE_ATTRIBUTE_BOOKMARK_AWARENESS_PANEL;
    message.data = {
      attribute: attribute,
      isBookmarked: attrConfig["awarenessPanel"]["isBookmarked"],
      eventX: null,
      eventY: null,
    };
    this.chatService.sendInteractionResponse(message);
    /* Prepare and Send New Message - End */
    if ($event) $event.stopPropagation();
  }

  /**
   * Toggle the `isExpanded` state for given attribute's awareness card.
   */
  onClickAccordion(attribute) {
    if (this.appConfig[this.global.appMode]["attributes"][attribute]["awarenessPanel"]["isExpanded"]) {
      this.collapseAccordion(attribute);
    } else {
      this.expandAccordion(attribute);
    }
  }

  /**
   * Mark all attribute cards in the AwarenessPanel to be `visible`
   */
  expandAccordion(attribute = null) {
    let dataset = this.appConfig[this.global.appMode];
    if (attribute == null) {
      dataset.attributeList.forEach((attr) => {
        dataset["attributes"][attr]["awarenessPanel"]["isExpanded"] = true;
      });
      $("#awarenessaccordion .collapse").addClass("show");
      this.updateAwarenessPanel(); // Refresh the awareness panel visualizations

      /* Prepare and Send New Message - Start */
      let message = this.utilsService.initializeNewMessage(this);
      message.interactionType = InteractionTypes.TOGGLE_ALL_ATTRIBUTE_ACCORDION_AWARENESS_PANEL;
      message.data = {
        isExpanded: true,
        eventX: null,
        eventY: null,
      };
      this.chatService.sendInteractionResponse(message);
      /* Prepare and Send New Message - End */
    } else {
      dataset["attributes"][attribute]["awarenessPanel"]["isExpanded"] = true;
      $("#awarenesscollapse-" + dataset["attributes"][attribute]["cleaned"]).addClass("show");
      this.updateAwarenessPanel(attribute); // Refresh the awareness panel visualizations just for this attribute

      /* Prepare and Send New Message - Start */
      let message = this.utilsService.initializeNewMessage(this);
      message.interactionType = InteractionTypes.TOGGLE_ATTRIBUTE_ACCORDION_AWARENESS_PANEL;
      message.data = {
        attribute: attribute,
        isExpanded: dataset["attributes"][attribute]["awarenessPanel"]["isExpanded"],
        eventX: null,
        eventY: null,
      };
      this.chatService.sendInteractionResponse(message);
      /* Prepare and Send New Message - End */
    }
  }

  /**
   * Mark all attribute cards in the AwarenessPanel to be `hidden`
   */
  collapseAccordion(attribute = null) {
    let dataset = this.appConfig[this.global.appMode];
    if (attribute == null) {
      dataset.attributeList.forEach((attr) => {
        dataset["attributes"][attr]["awarenessPanel"]["isExpanded"] = false;
      });
      $("#awarenessaccordion .collapse").removeClass("show");
      this.updateAwarenessPanel(); // Refresh the awareness panel visualizations

      /* Prepare and Send New Message - Start */
      let message = this.utilsService.initializeNewMessage(this);
      message.interactionType = InteractionTypes.TOGGLE_ALL_ATTRIBUTE_ACCORDION_AWARENESS_PANEL;
      message.data = {
        isExpanded: false,
        eventX: null,
        eventY: null,
      };
      this.chatService.sendInteractionResponse(message);
      /* Prepare and Send New Message - End */
    } else {
      dataset["attributes"][attribute]["awarenessPanel"]["isExpanded"] = false;
      $("#awarenesscollapse-" + dataset["attributes"][attribute]["cleaned"]).removeClass("show");
      this.updateAwarenessPanel(attribute); // Refresh the awareness panel visualizations just for this attribute

      /* Prepare and Send New Message - Start */
      let message = this.utilsService.initializeNewMessage(this);
      message.interactionType = InteractionTypes.TOGGLE_ATTRIBUTE_ACCORDION_AWARENESS_PANEL;
      message.data = {
        attribute: attribute,
        isExpanded: dataset["attributes"][attribute]["awarenessPanel"]["isExpanded"],
        eventX: null,
        eventY: null,
      };
      this.chatService.sendInteractionResponse(message);
      /* Prepare and Send New Message - End */
    }
  }

  /**
   * Swaps x and y attributes in the app config.
   */
  swapXY() {
    let dataset = this.appConfig[this.global.appMode];
    let xVar = dataset["xVar"];
    dataset["xVar"] = dataset["yVar"];
    dataset["yVar"] = xVar;
    this.updateVis();

    /* Prepare and Send New Message - Start */
    let message = this.utilsService.initializeNewMessage(this);
    message.interactionType = InteractionTypes.SWAP_AXES_ATTRIBUTES;
    message.data = {
      x: dataset["xVar"],
      y: dataset["yVar"],
      eventX: null,
      eventY: null,
    };
    this.chatService.sendInteractionResponse(message);
    /* Prepare and Send New Message - End */
  }

  /**
   * Enables filter for a specific attribute.
   */
  addFilter(attribute) {
    let dataset = this.appConfig[this.global.appMode];
    dataset["attributes"][attribute]["filter"] = true;
    dataset["attributeInteracted"][attribute] += 1;

    /* Prepare and Send New Message - Start */
    let message = this.utilsService.initializeNewMessage(this);
    message.interactionType = InteractionTypes.ADD_FILTER;
    message.data = {
      attribute: attribute,
      eventX: null,
      eventY: null,
    };
    this.chatService.sendInteractionResponse(message);
    /* Prepare and Send New Message - End */
  }

  /**
   * Disables a filter and resets the visualization.
   */
  removeFilter(attribute, updateVis = true, sendMessage = true) {
    let dataset = this.appConfig[this.global.appMode];
    let attrConfig = dataset["attributes"][attribute];
    if (this.utilsService.isMeasure(dataset, attribute, "N")) {
      attrConfig["filterModel"] = attrConfig["types"];
    } else if (this.utilsService.isMeasure(dataset, attribute, "O")) {
      attrConfig["filterModel"] = attrConfig["types"];
    } else if (this.utilsService.isMeasure(dataset, attribute, "Q")) {
      attrConfig["filterModel"] = [attrConfig["min"], attrConfig["max"]];
    } else if (this.utilsService.isMeasure(dataset, attribute, "T")) {
      attrConfig["filterModel"] = [attrConfig["min"], attrConfig["max"]];
    }

    attrConfig["filter"] = false;
    if (updateVis) this.updateVis();

    if (sendMessage) {
      /* Prepare and Send New Message - Start */
      let message = this.utilsService.initializeNewMessage(this);
      message.interactionType = InteractionTypes.REMOVE_FILTER;
      message.data = {
        attribute: attribute,
        eventX: null,
        eventY: null,
      };
      this.chatService.sendInteractionResponse(message);
      /* Prepare and Send New Message - End */
    }
  }

  /**
   * Disable all filters and reset the visualization.
   */
  removeFilters(updateVis = true) {
    this.appConfig[this.global.appMode].attributeList.forEach((attribute) =>
      this.removeFilter(attribute, false, false)
    );
    if (updateVis) this.updateVis();

    /* Prepare and Send New Message - Start */
    let message = this.utilsService.initializeNewMessage(this);
    message.interactionType = InteractionTypes.REMOVE_ALL_FILTERS;
    message.data = {
      eventX: null,
      eventY: null,
    };
    this.chatService.sendInteractionResponse(message);
    /* Prepare and Send New Message - End */
  }

  /**
   * Reset all encodings.
   */
  resetAllEncodings() {
    this.onChangeChart(null, true, false);
    this.onChangeAttribute(null, "x_axis", true, false);
    this.onChangeAttribute(null, "y_axis", true, false);

    // ToDo:- Revisit this code-block when the onChangeColorByMode is available by default for all appModes.
    if (this.global.appMode == "ADMIN") {
      this.onChangeVISColorByMode(null, true, false);
      this.onChangeAttributeColorByMode(null, true, false);
    }
    this.updateVis(); // only update the vis after all encodings are reset

    /* Prepare and Send New Message - Start */
    let message = this.utilsService.initializeNewMessage(this);
    message.interactionType = InteractionTypes.REMOVE_ALL_ENCODINGS;
    message.data = {
      eventX: null,
      eventY: null,
    };
    this.chatService.sendInteractionResponse(message);
    /* Prepare and Send New Message - End */
  }

  onChangeChart(event, reset = false, updateVis = true) {
    let dataset = this.appConfig[this.global.appMode];
    if (reset) dataset["chartType"] = null;
    this.currentPlotType = dataset["chartType"];
    if (updateVis) {
      initializePlotInstance(this, this.currentPlotType);
      this.updateVis();
    }
    if (!reset) {
      /* Prepare and Send New Message - Start */
      let message = this.utilsService.initializeNewMessage(this);
      message.interactionType = InteractionTypes.CHANGE_CHART_TYPE;
      message.data = {
        chartChanged: dataset["chartType"],
        x: dataset["xVar"],
        y: dataset["yVar"],
        eventX: null,
        eventY: null,
      };
      this.chatService.sendInteractionResponse(message);
      /* Prepare and Send New Message - End */
    }
  }

  onChangeAttribute(event, axis, reset = false, updateVis = true) {
    let dataset = this.appConfig[this.global.appMode];
    switch (axis) {
      case "x_axis":
        if (reset) dataset["xVar"] = null;
        if (dataset["xVar"]) dataset["attributeInteracted"][dataset["xVar"]] += 1;
        break;
      case "y_axis":
        if (reset) dataset["yVar"] = null;
        if (dataset["yVar"]) dataset["attributeInteracted"][dataset["yVar"]] += 1;
        break;
    }
    if (updateVis) {
      initializePlotInstance(this, this.currentPlotType);
      this.updateVis();
    }
    if (!reset) {
      /* Prepare and Send New Message - Start */
      let message = this.utilsService.initializeNewMessage(this);
      message.interactionType = InteractionTypes.CHANGE_AXIS_ATTRIBUTE;
      message.data = {
        axisChanged: axis,
        x: dataset["xVar"],
        y: dataset["yVar"],
        eventX: null,
        eventY: null,
      };
      this.chatService.sendInteractionResponse(message);
      /* Prepare and Send New Message - End */
    }
  }

  onChangeAggregation(event, updateVis = true) {
    let dataset = this.appConfig[this.global.appMode];
    /* Prepare and Send New Message - Start */
    let message = this.utilsService.initializeNewMessage(this);
    message.interactionType = InteractionTypes.CHANGE_AGGREGATION;
    message.data = {
      aggChanged: dataset["aggType"],
      x: dataset["xVar"],
      y: dataset["yVar"],
      eventX: null,
      eventY: null,
    };
    this.chatService.sendInteractionResponse(message);
    /* Prepare and Send New Message - End */
    if (updateVis) {
      initializePlotInstance(this, this.currentPlotType);
      this.updateVis();
    }
  }

  onChangeAttributeColorByMode(event, reset = false, updateVis = true) {
    if (!reset) {
      /* Prepare and Send New Message - Start */
      let message = this.utilsService.initializeNewMessage(this);
      message.interactionType = InteractionTypes.CHANGE_ATTRIBUTE_COLOR_BY_MODE;
      message.data = {
        colorBy: event,
        eventX: null,
        eventY: null,
      };
      this.chatService.sendInteractionResponse(message);
      /* Prepare and Send New Message - End */
    }
  }

  onChangeVISColorByMode(event, reset = false, updateVis = true) {
    let dataset = this.appConfig[this.global.appMode];
    if (reset) dataset["colorByMode"] = null;
    if (updateVis) {
      initializePlotInstance(this, this.currentPlotType);
      this.updateVis();
    }
    if (!reset) {
      /* Prepare and Send New Message - Start */
      let message = this.utilsService.initializeNewMessage(this);
      message.interactionType = InteractionTypes.CHANGE_VIS_COLOR_BY_MODE;
      message.data = {
        colorBy: dataset["colorByMode"],
        eventX: null,
        eventY: null,
      };
      this.chatService.sendInteractionResponse(message);
      /* Prepare and Send New Message - End */
    }
  }

  onChangeFilter(attribute, changeType) {
    let dataset = this.appConfig[this.global.appMode];
    dataset["attributeInteracted"][attribute] += 1;
    /* Prepare and Send New Message - Start */
    let message = this.utilsService.initializeNewMessage(this);
    message.interactionType = InteractionTypes.CHANGE_FILTER;
    message.data = {
      attribute: attribute,
      value: dataset["attributes"][attribute]["filterModel"],
      filterType: changeType,
      eventX: null,
      eventY: null,
    };
    this.chatService.sendInteractionResponse(message);
    /* Prepare and Send New Message - End */
    this.updateVis();
  }

  /**
   * Sends mouseover for dataPoint to server when row is hovered on
   * in details table for bar/line/dot plots.
   */
  mouseoverRow(event, dataPoint) {
    let dataset = this.appConfig[this.global.appMode];
    let originalDatasetDict = this.userConfig["originalDatasetDict"];
    dataPoint["xVar"] = dataset["xVar"] == null ? null : originalDatasetDict[dataPoint["id"]][dataset["xVar"]];
    dataPoint["yVar"] = dataset["yVar"] == null ? null : originalDatasetDict[dataPoint["id"]][dataset["yVar"]];
    this.utilsService.mouseoverItem(this, event, dataPoint);
  }

  /**
   * Sends mouseout for dataPoint to server when row is hovered off
   * in details table for bar/line/dot plots.
   */
  mouseoutRow(event, dataPoint) {
    let dataset = this.appConfig[this.global.appMode];
    let originalDatasetDict = this.userConfig["originalDatasetDict"];
    dataPoint["xVar"] = dataset["xVar"] == null ? null : originalDatasetDict[dataPoint["id"]][dataset["xVar"]];
    dataPoint["yVar"] = dataset["yVar"] == null ? null : originalDatasetDict[dataPoint["id"]][dataset["yVar"]];
    this.utilsService.mouseoutItem(this, event, dataPoint);
  }

  /**
   * SORT the incoming attribute panel array based on the sort by parameter
   */
  customSortAttributePanel(array) {
    let dataset = this.appConfig[this.global.appMode];
    let arrayCopy = JSON.parse(JSON.stringify(array));
    switch (this.userConfig["attributeSortByMode"]) {
      case "default":
        break;
      case "reverse-dtype":
        arrayCopy = [];
        arrayCopy.push(...dataset["attributeDatatypeList"]["N"]);
        arrayCopy.push(...dataset["attributeDatatypeList"]["O"]);
        arrayCopy.push(...dataset["attributeDatatypeList"]["T"]);
        arrayCopy.push(...dataset["attributeDatatypeList"]["Q"]);
        break;
      case "dtype":
        arrayCopy = [];
        arrayCopy.push(...dataset["attributeDatatypeList"]["Q"]);
        arrayCopy.push(...dataset["attributeDatatypeList"]["T"]);
        arrayCopy.push(...dataset["attributeDatatypeList"]["O"]);
        arrayCopy.push(...dataset["attributeDatatypeList"]["N"]);
        break;
      case "Focus 0-1":
        arrayCopy.sort((n1, n2) => {
          return dataset["attributeInteracted"][n1] - dataset["attributeInteracted"][n2];
        });
        break;
      case "Focus 1-0":
        arrayCopy.sort((n1, n2) => {
          return dataset["attributeInteracted"][n2] - dataset["attributeInteracted"][n1];
        });
        break;
      case "A-Z, 0-9":
        arrayCopy.sort((n1, n2) => {
          const a1 = n1.toLowerCase(),
            a2 = n2.toLowerCase();
          if (a1 < a2)
            //sort string ascending
            return -1;
          if (a1 > a2) return 1;
          return 0; // default return value (no sorting)
        });
        break;
      case "Z-A, 9-0":
        arrayCopy.sort((n1, n2) => {
          const a1 = n1.toLowerCase(),
            a2 = n2.toLowerCase();
          if (a1 < a2)
            //sort string descending
            return 1;
          if (a1 > a2) return -1;
          return 0; // default return value (no sorting)
        });
        break;
      default:
        console.log(`Invalid attribute panel Sort By option; Do nothing.`);
        break;
    }
    // remove primary Key and label Key from awareness panel
    arrayCopy = arrayCopy.filter((el) => el !== dataset["primaryKey"] && el !== dataset["labelKey"]);
    return arrayCopy;
  }

  /**
   * SORT the incoming awareness panel array based on the sort by parameter
   */
  customSortAwarenessPanel(array) {
    if (this.global.appType == "CONTROL") return array;
    let dataset = this.appConfig[this.global.appMode];
    // remove primary Key and label Key from awareness panel
    const arrayCopy = JSON.parse(JSON.stringify(array)).filter(
      (el) => el !== dataset["primaryKey"] && el !== dataset["labelKey"]
    );
    switch (this.userConfig["awarenessSortByMode"]) {
      case "A-Z, 0-9":
        arrayCopy.sort((n1, n2) => {
          const a1 = n1.toLowerCase(),
            a2 = n2.toLowerCase();
          if (a1 < a2)
            //sort string ascending
            return -1;
          if (a1 > a2) return 1;
          return 0; // default return value (no sorting)
        });
        break;
      case "Z-A, 9-0":
        arrayCopy.sort((n1, n2) => {
          const a1 = n1.toLowerCase(),
            a2 = n2.toLowerCase();
          if (a1 < a2)
            //sort string descending
            return 1;
          if (a1 > a2) return -1;
          return 0; // default return value (no sorting)
        });
        break;
      case "Bias 0-1":
        arrayCopy.sort((n1, n2) => {
          return dataset["attributeBiasValues"][n1] - dataset["attributeBiasValues"][n2];
        });
        break;
      case "Bias 1-0":
        arrayCopy.sort((n1, n2) => {
          return dataset["attributeBiasValues"][n2] - dataset["attributeBiasValues"][n1];
        });
        break;
      default:
        console.log(`Invalid awareness Panel Sort By option; Do nothing.`);
        break;
    }
    return arrayCopy;
  }
}

/** ======================= CONVENIENCE FUNCTIONS ========================= */

/**
 * Set default values for each data set in AppConfig.
 */
function initializeAppModes(appConfig) {
  // iterate datasets, setting dataset-level configuration keys
  for (var appMode in appConfig) {
    if (appConfig.hasOwnProperty(appMode)) {
      let dataset = appConfig[appMode];
      (dataset["chartType"] = null),
        (dataset["xVar"] = null),
        (dataset["yVar"] = null),
        (dataset["aggType"] = "avg"),
        (dataset["colorByMode"] = "rel"),
        (dataset["hoveredObject"] = { hovered: false }),
        (dataset["hoveredObjects"] = { binName: null, binAttr: null, points: {} }),
        (dataset["selectedObject"] = { selected: false }),
        (dataset["selectedObjects"] = {}),
        (dataset["selectedGroups"] = {}),
        (dataset["attributeInteracted"] = {}),
        (dataset["ratioAttributeInteracted"] = {}),
        (dataset["attributeBiasValues"] = {}),
        (dataset["sumAttributeBiasValues"] = 0),
        (dataset["attributeDistribution"] = {
          original: {},
          interacted: {},
        }),
        (dataset["attributeCoverage"] = {
          interacted: {},
        });
      // iterate attributes in dataset, setting attribute-level configuration keys
      for (var key in dataset["attributes"]) {
        if (dataset["attributes"].hasOwnProperty(key)) {
          let attribute = dataset["attributes"][key];
          (attribute["filter"] = false),
            (attribute["task"] = false),
            (attribute["taskModel"] = {}),
            (attribute["taskType"] = "proportional"),
            (attribute["awarenessPanel"] = {
              isExpanded: false,
              isBookmarked: false,
            });
        }
      }
    }
  }
  return appConfig;
}

/**
 * Return new plot instance.
 */
function createPlotInstance(context, plotObject) {
  return new plotObject(
    context.utilsService,
    context.chatService,
    context.global,
    context.userConfig,
    context.appConfig
  );
}

/**
 * Initializes plot based on incoming chart type.
 */
function initializePlotInstance(context, chartType) {
  switch (chartType) {
    case "scatterplot":
      // use VIS Matrix to determine which version to initialize
      let dataset = context.appConfig[context.global.appMode];
      const xVar = dataset["xVar"];
      const yVar = dataset["yVar"];
      const xIsQ = context.utilsService.isMeasure(dataset, xVar, "Q");
      const yIsQ = context.utilsService.isMeasure(dataset, yVar, "Q");
      if (!(xVar || yVar) || xIsQ || yIsQ) {
        context.currentPlotInstance = "scatterplot";
        context.scatterPlotInstance.initialize();
      } else {
        context.currentPlotInstance = "dotplot";
        context.dotPlotInstance.initialize();
      }
      break;
    case "stripplot":
      context.currentPlotInstance = "stripplot";
      context.stripPlotInstance.initialize();
      break;
    case "barchart":
      context.currentPlotInstance = "barchart";
      context.barChartInstance.initialize();
      break;
    case "linechart":
      context.currentPlotInstance = "linechart";
      context.lineChartInstance.initialize();
      break;
    case null:
      context.currentPlotInstance = null;
      break;
    default:
      console.log(`Invalid plot type '${chartType}'`);
      break;
  }
}

/**
 * Set configObject Y Axis Title and Format and return configObject.
 */
function setYAxisTitle(awarenessMode, configObject) {
  let layer0YAxis = configObject["layer"][0]["encoding"]["y"]["axis"];
  let layer1YAxis = configObject["layer"][1]["encoding"]["y"]["axis"];
  switch (awarenessMode) {
    case "Raw":
      layer0YAxis["title"] = "# Datapoints";
      layer1YAxis["title"] = "# Datapoints";
      layer0YAxis["format"] = null;
      layer1YAxis["format"] = null;
      break;
    case "Percentage":
      layer0YAxis["title"] = "Percentage";
      layer1YAxis["title"] = "Percentage";
      layer0YAxis["format"] = "%";
      layer1YAxis["format"] = "%";
      break;
  }
  return configObject;
}

/**
 * Uses canvas.measureText to compute and return the width of the given text of given font in pixels.
 *
 * @param {CanvasRenderingContext2D} context The context to render the text.
 * @param {String} text The text to be rendered.
 *
 * @see https://stackoverflow.com/questions/118241/calculate-text-width-with-javascript/21015393#21015393
 */
function getTextWidth(context, text) {
  const metrics = context.measureText(text);
  return metrics.width;
}

/**
 * Uses textarea to decode HTML characters
 *
 * @param {HTMLTextAreaElement} el The element to render the html text.
 * @param {String} html The text to be rendered.
 *
 * @see https://stackoverflow.com/a/7394787
 */
function decodeHtml(el, html) {
  el.innerHTML = html;
  return el.value;
}
