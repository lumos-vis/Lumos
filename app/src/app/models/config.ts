// libraries
import * as d3 from "d3";
import { Injectable } from "@angular/core";
// local
import { UtilsService } from "../services/utils.service";

var UtilsServiceObj = new UtilsService();
var prolificId = new URLSearchParams(window.location.search).get("PROLIFIC_PID");
var participantId = prolificId || UtilsServiceObj.generateRandomUniqueString(12);
var participantIdSource = prolificId ? "prolific" : "random";

export const divergentColorRange = ["#a5d6a7", "#eeeeee", "#ef9a9a"];
export const sequentialColorRange = ["#ffffff", "#3498db"];

@Injectable()
export class SessionPage {
  constructor(private utils: UtilsService) {}
  "app-practice": object = { completed: false, timestamp: 0 };
  "app-live": object = { completed: false, timestamp: 0 };
  "participantId": string = participantId;
  "participantIdSource": string = participantIdSource; // "prolific" | "random"
  "appMode": string = "credit_risk.csv"; // Name of the dataset
  "appLevel": string = "live"; // Practice / Live
  // "appType": string = this.utils.generateRandomAppType(); // CONTROL / AWARENESS
  "appType": string = "AWARENESS"; // CONTROL | ADMIN | AWARENESS
}

export const DeploymentConfig = Object.freeze({
  SERVER_URL: "https://lumos-llm.onrender.com/"
  // SERVER_URL: "http://localhost:3000"
});

/**
 * SUPPORTED INTERACTION TYPES
 */
export const enum InteractionTypes {
  // Attribute Accordion Open/Close
  TOGGLE_ATTRIBUTE_ACCORDION_AWARENESS_PANEL = "toggle_attribute_accordion_awareness_panel",
  TOGGLE_ALL_ATTRIBUTE_ACCORDION_AWARENESS_PANEL = "toggle_all_attribute_accordion_awareness_panel",

  // Attribute Panel Bookmark/Unbookmark 
  TOGGLE_ATTRIBUTE_BOOKMARK_AWARENESS_PANEL = "toggle_attribute_bookmark_awareness_panel",
  TOGGLE_ALL_ATTRIBUTE_BOOKMARK_AWARENESS_PANEL = "toggle_all_attribute_bookmark_awareness_panel",

  // Sorts
  CHANGE_ATTRIBUTE_PANEL_SORT = "attribute_panel_sort_changed",
  CHANGE_DISTRIBUTION_PANEL_SORT = "distribution_panel_sort_changed",

  // Filters
  ADD_FILTER = "filter_added",
  REMOVE_FILTER = "filter_removed",
  REMOVE_ALL_FILTERS = "all_filters_removed",
  CHANGE_FILTER = "filter_changed",

  // Encodings
  REMOVE_ALL_ENCODINGS = "all_encodings_removed",
  CHANGE_AXIS_ATTRIBUTE = "axis_attribute_changed",
  SWAP_AXES_ATTRIBUTES = "axes_attributes_swapped",
  CHANGE_AGGREGATION = "aggregation_changed",
  CHANGE_CHART_TYPE = "chart_type_changed",
  CHANGE_VIS_COLOR_BY_MODE = "vis_color_by_changed",
  CHANGE_ATTRIBUTE_COLOR_BY_MODE = "attribute_panel_color_by_changed",

  // VIS Interactions
  CLICK_ADD_ITEM = "click_add_item",
  CLICK_REMOVE_ITEM = "click_remove_item",
  CLICK_GROUP = "click_group",
  MOUSEOVER_ITEM = "mouseover_item",
  MOUSEOVER_GROUP = "mouseover_group",
  MOUSEOUT_ITEM = "mouseout_item",
  MOUSEOUT_GROUP = "mouseout_group",
}

/**
 * USER SPECIFIC SETTINGS
 */
export var UserConfig = {
  // Default settings for various parameters
  attributeSortByMode: "default",
  attributeColorByMode: "rel",
  attributeColorScale: "Sequential",
  awarenessMode: "Percentage",
  awarenessSortByMode: "Bias 1-0",
  awarenessColorScale: "Divergent",
  interpolateMode: "monotone",
  axisRescale: false,
  jitterScatterplotPoints: false,
  sizes: {
    awarenessPanel: {
      width: 0, // Set on ng Init.
      height: 100, // default
    },
  },
  filterMultiselectDropdownSettings: {
    singleSelection: false,
    enableCheckAll: false,
    allowSearchFilter: false,
  },
  awarenessLayerMultiselectDropdownSettings: {
    singleSelection: false,
    itemsShowLimit: 1,
    enableCheckAll: false,
    allowSearchFilter: false,
  },
  originalDataset: [], // set in main-activity/component.ts
  originalDatasetDict: {}, // set in main-activity/component.ts
  hoverTimer: null, // set in services/utils.service.ts
  hoverStartTime: 0, // set in services/utils.service.ts
  listHoverTimer: null, // set in services/utils.service.ts

  // Color scales for attributes and data points
  awarenessDivergentColorScale: d3
    .scaleLinear()
    .domain([0, 0.5, 1])
    .range(divergentColorRange as any),
  awarenessSequentialColorScale: d3
    .scaleLinear()
    .domain([0, 1])
    .range(sequentialColorRange as any),
  focusDivergentColorScale: d3
    .scaleLinear()
    .domain([0, 0.5, 1])
    .range(divergentColorRange as any),
  focusSequentialColorScale: d3
    .scaleLinear()
    .domain([0, 1])
    .range(sequentialColorRange as any),

  // Ordered Lists of all setting options
  charts: ["scatterplot", "stripplot", "barchart", "linechart"],
  aggregations: ["count", "avg", "min", "max", "sum"],
  awarenessModes: ["Percentage", "Raw"],
  colorByModes: ["abs", "rel", "binary"],
  colorScales: ["Divergent", "Sequential"],
  attributeControlSortByModes: ["default", "reverse-dtype", "dtype", "A-Z, 0-9", "Z-A, 9-0"],
  attributeSortByModes: ["default", "reverse-dtype", "dtype", "Focus 1-0", "Focus 0-1", "A-Z, 0-9", "Z-A, 9-0"],
  awarenessSortByModes: ["Bias 1-0", "Bias 0-1", "A-Z, 0-9", "Z-A, 9-0"],
  awarenessVisLayers: ["Data", "Focus", "Selection"],
  awarenessVisLayerTypes: ["Data", "Focus", "Selection"],
  interpolateModes: [
    "monotone",
    "linear",
    "step",
    "step-before",
    "step-after",
    "natural",
    "basis",
    "cardinal",
    "cardinal-open",
    "cardinal-closed",
  ],

  // Mappings of settings to display names
  chartMapping: {
    scatterplot: "Scatter Plot",
    stripplot: "Strip Plot",
    dotplot: "Dot Plot",
    barchart: "Bar Chart",
    linechart: "Line Chart",
  },
  aggregationMapping: {
    count: "Count",
    avg: "Average",
    min: "Minimum",
    max: "Maximum",
    sum: "Sum",
  },
  colorByModeMapping: {
    abs: "Absolute Freq",
    rel: "Relative Freq",
    binary: "Binary",
  },
  colorScaleMapping: {
    Divergent: "Divergent",
    Sequential: "Sequential",
  },
  sortByModeMapping: {
    default: "Default",
    dtype: "Data Type &#8593",
    "reverse-dtype": "Data Type &#8595",
    "Focus 1-0": "Focus &#8595",
    "Focus 0-1": "Focus &#8593",
    "Bias 1-0": "Different &#8594 Similar",
    "Bias 0-1": "Similar &#8594; Different",
    "A-Z, 0-9": "A-Z &#8226; 0-9",
    "Z-A, 9-0": "Z-A &#8226; 9-0",
  },
  datatypeIconMapping: {
    Q: {
      "fa-class": "fa-hashtag",
      "fa-unicode": "&#xf292;",
    },
    T: {
      "fa-class": "fa-calendar",
      "fa-unicode": "&#xf133;",
    },
    O: {
      "fa-class": "fa-font",
      "fa-unicode": "&#xf031;",
    },
    N: {
      "fa-class": "fa-font",
      "fa-unicode": "&#xf031;",
    },
  },
};

/**
 * DATASET SPECIFIC SETTINGS
 */
export const AppConfig = {
  /**
   * 1. Cars data set
   */
  "cars.csv": {
    dataset: "cars.csv",
    primaryKey: "id",
    labelKey: "name",
    orderedAttributeList: [
      "id",
      "name",
      "Length",
      "Width",
      "Height",
      "Number of Forward Gears",
      "Torque",
      "Horsepower",
      "City mpg",
      "Highway mpg",
      "Transmission",
      "Driveline",
      "Fuel Type",
    ],
    attributes: {
      Length: {
        name: "Length",
        datatype: "Q",
        max: -Infinity,
        step: 1,
        min: Infinity,
        filterModel: [0, 1],
      },
      Width: {
        name: "Width",
        datatype: "Q",
        max: -Infinity,
        step: 1,
        min: Infinity,
        filterModel: [0, 1],
      },
      Height: {
        name: "Height",
        datatype: "Q",
        max: -Infinity,
        step: 1,
        min: Infinity,
        filterModel: [0, 1],
      },
      "Number of Forward Gears": {
        name: "Forward Gears",
        datatype: "Q",
        max: -Infinity,
        step: 1,
        min: Infinity,
        filterModel: [0, 1],
      },
      Torque: {
        name: "Torque",
        datatype: "Q",
        max: -Infinity,
        step: 1,
        min: Infinity,
        filterModel: [0, 1],
      },
      Horsepower: {
        name: "Horsepower",
        datatype: "Q",
        max: -Infinity,
        step: 1,
        min: Infinity,
        filterModel: [0, 1],
      },
      "City mpg": {
        name: "City mpg",
        datatype: "Q",
        max: -Infinity,
        step: 1,
        min: Infinity,
        filterModel: [0, 1],
      },
      "Highway mpg": {
        name: "Highway mpg",
        datatype: "Q",
        max: -Infinity,
        step: 1,
        min: Infinity,
        filterModel: [0, 1],
      },
      Transmission: {
        name: "Transmission",
        datatype: "N",
        types: [],
        filterModel: [],
      },
      Driveline: {
        name: "Driveline",
        datatype: "N",
        types: [],
        filterModel: [],
      },
      "Fuel Type": {
        name: "Fuel Type",
        datatype: "N",
        types: [],
        filterModel: [],
      },
      id: {
        name: "ID",
        datatype: "N",
        types: [],
        filterModel: [],
      },
      name: {
        name: "Name",
        datatype: "N",
        types: [],
        filterModel: [],
      },
    },
  },
  /**
   * 2. Movies data set
   */
  "movies-w-year.csv": {
    dataset: "movies-w-year.csv",
    primaryKey: "id",
    labelKey: "Title",
    orderedAttributeList: [
      "id",
      "Title",
      "Genre",
      "Creative Type",
      "Content Rating",
      "Release Year",
      "Running Time",
      "Production Budget",
      "Worldwide Gross",
      "Rotten Tomatoes Rating",
      "IMDB Rating",
    ],
    attributes: {
      "Running Time": {
        name: "Running Time",
        datatype: "Q",
        max: -Infinity,
        step: 1,
        min: Infinity,
        filterModel: [0, 1],
      },
      "Rotten Tomatoes Rating": {
        name: "Rotten Tomatoes Rating",
        datatype: "Q",
        max: -Infinity,
        step: 0.1,
        min: Infinity,
        filterModel: [0, 1],
      },
      "IMDB Rating": {
        name: "IMDB Rating",
        datatype: "Q",
        max: -Infinity,
        step: 0.1,
        min: Infinity,
        filterModel: [0, 1],
      },
      "Worldwide Gross": {
        name: "Worldwide Gross",
        datatype: "Q",
        max: -Infinity,
        step: 1,
        min: Infinity,
        filterModel: [0, 1],
      },
      "Production Budget": {
        name: "Production Budget",
        datatype: "Q",
        max: -Infinity,
        step: 1,
        min: Infinity,
        filterModel: [0, 1],
      },
      "Release Year": {
        name: "Release Year",
        datatype: "T",
        max: -Infinity,
        step: 1,
        min: Infinity,
        filterModel: [0, 1],
      },
      id: {
        name: "id",
        datatype: "N",
        types: [],
        filterModel: [],
      },
      Title: {
        name: "Title",
        datatype: "N",
        types: [],
        filterModel: [],
      },
      Genre: {
        name: "Genre",
        datatype: "N",
        types: [],
        filterModel: [],
      },
      "Creative Type": {
        name: "Creative Type",
        datatype: "N",
        types: [],
        filterModel: [],
      },
      "Content Rating": {
        name: "Content Rating",
        datatype: "N",
        types: [],
        filterModel: [],
      },
    },
  },
  /**
   * 3. Cars with years data set
   */
  "cars-w-year.csv": {
    dataset: "cars-w-year.csv",
    primaryKey: "id",
    labelKey: "",
    orderedAttributeList: [
      "id",
      "Year",
      "Origin",
      "MPG",
      "Cylinders",
      "Weight",
      "Horsepower",
      "Acceleration",
      "Displacement",
    ],
    attributes: {
      MPG: {
        name: "MPG",
        datatype: "Q",
        max: -Infinity,
        step: 1,
        min: Infinity,
        filterModel: [0, 1],
      },
      Cylinders: {
        name: "Cylinders",
        datatype: "Q",
        max: -Infinity,
        step: 1,
        min: Infinity,
        filterModel: [0, 1],
      },
      Displacement: {
        name: "Displacement",
        datatype: "Q",
        max: -Infinity,
        step: 1,
        min: Infinity,
        filterModel: [0, 1],
      },
      Horsepower: {
        name: "Horsepower",
        datatype: "Q",
        max: -Infinity,
        step: 1,
        min: Infinity,
        filterModel: [0, 1],
      },
      Weight: {
        name: "Weight",
        datatype: "Q",
        max: -Infinity,
        step: 1,
        min: Infinity,
        filterModel: [0, 1],
      },
      Acceleration: {
        name: "Aceleration",
        datatype: "Q",
        max: -Infinity,
        step: 1,
        min: Infinity,
        filterModel: [0, 1],
      },
      Year: {
        name: "Year",
        datatype: "T",
        max: -Infinity,
        step: 1,
        min: Infinity,
        filterModel: [0, 1],
      },
      id: {
        name: "id",
        datatype: "N",
        types: [],
        filterModel: [],
      },
      Origin: {
        name: "Origin",
        datatype: "N",
        types: [],
        filterModel: [],
      },
    },
  },
  /**
   * 4. Housing data set
   */
  "housing.csv": {
    dataset: "housing.csv",
    primaryKey: "id",
    labelKey: "",
    orderedAttributeList: [
      "id",
      "Fireplaces",
      "Lot Area",
      "Price",
      "Rooms",
      "Satisfaction",
      "Year",
      "Central Air",
      "Fence Type",
      "Foundation Type",
      "Garage Type",
      "Heating Type",
      "Home Type",
      "Lot Config",
      "Roof Style",
    ],
    attributes: {
      Rooms: {
        name: "Rooms",
        datatype: "Q",
        max: -Infinity,
        step: 1,
        min: Infinity,
        filterModel: [0, 1],
      },
      Fireplaces: {
        name: "Fireplaces",
        datatype: "Q",
        max: -Infinity,
        step: 1,
        min: Infinity,
        filterModel: [0, 1],
      },
      Price: {
        name: "Price",
        datatype: "Q",
        max: -Infinity,
        step: 1,
        min: Infinity,
        filterModel: [0, 1],
      },
      Satisfaction: {
        name: "Satisfaction",
        datatype: "Q",
        max: -Infinity,
        step: 1,
        min: Infinity,
        filterModel: [0, 1],
      },
      "Lot Area": {
        name: "Lot Area",
        datatype: "Q",
        max: -Infinity,
        step: 1,
        min: Infinity,
        filterModel: [0, 1],
      },
      Year: {
        name: "Year",
        datatype: "T",
        max: -Infinity,
        step: 1,
        min: Infinity,
        filterModel: [0, 1],
      },
      id: {
        name: "id",
        datatype: "N",
        types: [],
        filterModel: [],
      },
      "Lot Config": {
        name: "Lot Config",
        datatype: "N",
        types: [],
        filterModel: [],
      },
      "Home Type": {
        name: "Home Type",
        datatype: "N",
        types: [],
        filterModel: [],
      },
      "Roof Style": {
        name: "Roof Style",
        datatype: "N",
        types: [],
        filterModel: [],
      },
      "Foundation Type": {
        name: "Foundation Type",
        datatype: "N",
        types: [],
        filterModel: [],
      },
      "Heating Type": {
        name: "Heating Type",
        datatype: "N",
        types: [],
        filterModel: [],
      },
      "Central Air": {
        name: "Central Air",
        datatype: "N",
        types: [],
        filterModel: [],
      },
      "Garage Type": {
        name: "Garage Type",
        datatype: "N",
        types: [],
        filterModel: [],
      },
      "Fence Type": {
        name: "Fence Type",
        datatype: "N",
        types: [],
        filterModel: [],
      },
    },
  },
  /**
   * 5. European soccer players data set
   */
  "euro.csv": {
    dataset: "euro.csv",
    primaryKey: "id",
    labelKey: "Name",
    orderedAttributeList: ["id", "Name", "Age", "Country", "Club", "Position", "Foot", "Goals", "Salary"],
    attributes: {
      Age: {
        name: "Age",
        datatype: "Q",
        max: -Infinity,
        step: 1,
        min: Infinity,
        filterModel: [0, 1],
      },
      Salary: {
        name: "Salary",
        datatype: "Q",
        max: -Infinity,
        step: 1,
        min: Infinity,
        filterModel: [0, 1],
      },
      Goals: {
        name: "Goals",
        datatype: "Q",
        max: -Infinity,
        step: 1,
        min: Infinity,
        filterModel: [0, 1],
      },
      id: {
        name: "id",
        datatype: "N",
        types: [],
        filterModel: [],
      },
      Name: {
        name: "Name",
        datatype: "N",
        types: [],
        filterModel: [],
      },
      Position: {
        name: "Position",
        datatype: "N",
        types: [],
        filterModel: [],
      },
      Foot: {
        name: "Foot",
        datatype: "N",
        types: [],
        filterModel: [],
      },
      Club: {
        name: "Club",
        datatype: "N",
        types: [],
        filterModel: [],
      },
      Country: {
        name: "Country",
        datatype: "N",
        types: [],
        filterModel: [],
      },
    },
  },
  /**
   * 6. Colleges data set
   */
  "colleges.csv": {
    dataset: "colleges.csv",
    primaryKey: "id",
    labelKey: "Name",
    orderedAttributeList: [
      "id",
      "Name",
      "Control",
      "Region",
      "Locale",
      "Admission Rate",
      "ACT Median",
      "SAT Average",
      "Population",
      "Average Cost",
      "Expenditure",
      "Average Faculty Salary",
      "Median Debt",
      "Median Family Income",
      "Median Earnings",
    ],
    attributes: {
      "Admission Rate": {
        name: "Admission Rate",
        datatype: "Q",
        max: -Infinity,
        step: 1,
        min: Infinity,
        filterModel: [0, 1],
      },
      "ACT Median": {
        name: "ACT Median",
        datatype: "Q",
        max: -Infinity,
        step: 1,
        min: Infinity,
        filterModel: [0, 1],
      },
      "SAT Average": {
        name: "SAT Average",
        datatype: "Q",
        max: -Infinity,
        step: 1,
        min: Infinity,
        filterModel: [0, 1],
      },
      Population: {
        name: "Population",
        datatype: "Q",
        max: -Infinity,
        step: 1,
        min: Infinity,
        filterModel: [0, 1],
      },
      "Average Cost": {
        name: "Average Cost",
        datatype: "Q",
        max: -Infinity,
        step: 1,
        min: Infinity,
        filterModel: [0, 1],
      },
      Expenditure: {
        name: "Expenditure",
        datatype: "Q",
        max: -Infinity,
        step: 1,
        min: Infinity,
        filterModel: [0, 1],
      },
      "Average Faculty Salary": {
        name: "Average Faculty Salary",
        datatype: "Q",
        max: -Infinity,
        step: 1,
        min: Infinity,
        filterModel: [0, 1],
      },
      "Median Debt": {
        name: "Median Debt",
        datatype: "Q",
        max: -Infinity,
        step: 1,
        min: Infinity,
        filterModel: [0, 1],
      },
      "Median Family Income": {
        name: "Median Family Income",
        datatype: "Q",
        max: -Infinity,
        step: 1,
        min: Infinity,
        filterModel: [0, 1],
      },
      "Median Earnings": {
        name: "Median Earnings",
        datatype: "Q",
        max: -Infinity,
        step: 1,
        min: Infinity,
        filterModel: [0, 1],
      },
      id: {
        name: "id",
        datatype: "N",
        types: [],
        filterModel: [],
      },
      Name: {
        name: "Name",
        datatype: "N",
        types: [],
        filterModel: [],
      },
      Control: {
        name: "Control",
        datatype: "N",
        types: [],
        filterModel: [],
      },
      Region: {
        name: "Region",
        datatype: "N",
        types: [],
        filterModel: [],
      },
      Locale: {
        name: "Locale",
        datatype: "N",
        types: [],
        filterModel: [],
      },
    },
  },
  /**
   * 7. Credit Risk data set
   */
  "credit_risk.csv": {
    dataset: "credit_risk.csv",
    primaryKey: "id",
    labelKey: "",
    orderedAttributeList: [
      "id",
      "Age",
      "Home Ownership Type",
      "Annual Income",
      "Employment Length",
      "Credit History",
      "Default History",
      "Loan Intent",
      "Loan Amount",
      "Loan Interest Rate",
    ],
    attributes: {
      Age: {
        name: "Age",
        datatype: "Q",
        max: -Infinity,
        step: 1,
        min: Infinity,
        filterModel: [0, 1],
      },
      "Annual Income": {
        name: "Annual Income",
        datatype: "Q",
        max: -Infinity,
        step: 1,
        min: Infinity,
        filterModel: [0, 1],
      },
      "Employment Length": {
        name: "Employment Length",
        datatype: "Q",
        max: -Infinity,
        step: 1,
        min: Infinity,
        filterModel: [0, 1],
      },
      "Credit History": {
        name: "Credit History",
        datatype: "Q",
        max: -Infinity,
        step: 1,
        min: Infinity,
        filterModel: [0, 1],
      },
      "Loan Amount": {
        name: "Loan Amount",
        datatype: "Q",
        max: -Infinity,
        step: 1,
        min: Infinity,
        filterModel: [0, 1],
      },
      "Loan Interest Rate": {
        name: "Loan Interest Rate",
        datatype: "Q",
        max: -Infinity,
        step: 0.01,
        min: Infinity,
        filterModel: [0, 1],
      },
      id: {
        name: "id",
        datatype: "N",
        types: [],
        filterModel: [],
      },
      "Home Ownership Type": {
        name: "Home Ownership Type",
        datatype: "N",
        types: [],
        filterModel: [],
      },
      "Default History": {
        name: "Default History",
        datatype: "N",
        types: [],
        filterModel: [],
      },
      "Loan Intent": {
        name: "Loan Intent",
        datatype: "N",
        types: [],
        filterModel: [],
      },
    },
  },
};
