// libraries
import * as d3 from "d3";
import $ from "jquery";
// local
import { LineChartConfig } from "src/app/models/viz";
import { sequentialColorRange, SessionPage } from "src/app/models/config";
import { UtilsService } from "src/app/services/utils.service";
import { ChatService } from "src/app/services/socket.service";

export class LineChart {
  lineChartConfig;
  plotWidth;
  plotHeight;
  plotGroup;

  constructor(
    public utilsService: UtilsService,
    public chatService: ChatService,
    public global: SessionPage,
    public userConfig,
    public appConfig
  ) {
    this.lineChartConfig = new LineChartConfig();
  }

  /**
   * Create variables needed to draw and update plot.
   */
  initialize() {
    let context = this;
    const container = "#plot_container";
    const width = $(container).parent().width();
    const height = $(container).parent().height();
    const plotMargins = { top: 50, bottom: 50, left: 60, right: 30 };

    context.plotWidth = width - plotMargins.left - plotMargins.right;
    context.plotHeight = height - plotMargins.top - plotMargins.bottom;

    $(container).empty();

    // Add containing SVG
    let svg = d3.select(container).append("svg").attr("width", width).attr("height", height);

    // Add linear gradient to SVG definition for use in color scale FIRST
    let grad = svg
      .append("defs")
      .append("linearGradient")
      .attr("id", "grad")
      .attr("x1", "0%")
      .attr("x2", "100%")
      .attr("y1", "0%")
      .attr("y2", "0%");
    grad
      .selectAll("stop")
      .data(sequentialColorRange)
      .enter()
      .append("stop")
      .style("stop-color", (d) => d.toString())
      .attr("offset", (_, i) => 100 * (i / (sequentialColorRange.length - 1)) + "%");

    // Add plot group
    context.plotGroup = svg
      .append("g")
      .classed("plot", true)
      .attr("transform", `translate(${plotMargins.left}, ${plotMargins.top})`);

    // Add X and Y axis groups
    context.lineChartConfig.yAxisGroup = context.plotGroup.append("g").classed("y", true).classed("axis", true);
    context.lineChartConfig.xAxisGroup = context.plotGroup
      .append("g")
      .classed("x", true)
      .classed("axis", true)
      .attr("transform", `translate(${0}, ${context.plotHeight})`);

    // Add line group (with pointer-events enabled)
    context.lineChartConfig.lineGroup = context.plotGroup
      .append("g")
      .classed("line", true)
      .style("pointer-events", "fill");

    // Add bounding box to line group for event listeners
    context.lineChartConfig.lineGroup
      .append("rect")
      .attr("class", "event-bbox")
      .attr("height", context.plotHeight)
      .attr("width", context.plotWidth)
      .attr("visibility", "hidden");

    // draw intersection line (hidden at first)
    context.lineChartConfig.lineGroup
      .append("line")
      .attr("y2", context.plotHeight)
      .attr("class", "intersect-line")
      .attr("stroke", "currentColor")
      .attr("stroke-dasharray", "5,5")
      .style("display", "none");

    // add empty path element to be drawn on update
    context.lineChartConfig.lineGroup
      .append("path")
      .attr("class", "connect-line")
      .attr("fill", "none")
      .attr("stroke", "currentColor");

    // Add legend group, text and gradient rectangle
    context.lineChartConfig.legendGroup = context.plotGroup.append("g").classed("legend", true);
    if (true) {
      let xPos = context.plotWidth; // x position of element, gets updated dynamically
      const pad = 5; // padding between elements
      const gradRectWidth = context.plotWidth / 5; // width of gradient rectangle
      const el = context.lineChartConfig.legendGroup
        .append("text")
        .attr("transform", `translate(${xPos}, ${(-5 / 8) * plotMargins.top})`)
        .attr("text-anchor", "end")
        .text("More Focus");
      xPos -= Math.abs(el.node().getBBox()["x"]) + gradRectWidth + pad;
      context.lineChartConfig.legendGroup
        .append("rect")
        .attr("transform", `translate(${xPos}, ${(-3 / 4) * plotMargins.top})`)
        .attr("width", gradRectWidth)
        .attr("height", (1 / 8) * plotMargins.top)
        .style("rx", "4")
        .style("fill", "url(#grad)");
      xPos -= pad;
      context.lineChartConfig.legendGroup
        .append("text")
        .attr("transform", `translate(${xPos}, ${(-5 / 8) * plotMargins.top})`)
        .attr("text-anchor", "end")
        .text("Less Focus");
    }

    // Create unsupported text to display if chart cannot render
    context.lineChartConfig.unsupportedMessage = `
      <tspan>Try using a temporal</tspan>
        (<tspan style="font-family: 'Font Awesome 5 Free'; font-weight: 800 !important">&#xf133;</tspan>)
      <tspan x="0" dy="1.2em">attribute on the <tspan style="font-weight: 800 !important">X</tspan> axis!</tspan>`;
  }

  /**
   * Calculate new values and re-draw plot.
   */
  update() {
    let context = this;
    let originalDatasetDict = context.userConfig["originalDatasetDict"];
    let dataset = context.appConfig[context.global.appMode];
    const PK = dataset["primaryKey"];

    // if there's no dataset don't update the chart
    if (!originalDatasetDict) return;

    // Clear unsupported message
    context.lineChartConfig.lineGroup.select(".unsupported-text").remove();

    // create raw data object
    let rawData = Object.keys(originalDatasetDict).map((id) => {
      return {
        ...originalDatasetDict[id],
        xVar: dataset["xVar"] == null ? null : originalDatasetDict[id][dataset["xVar"]],
        yVar: dataset["yVar"] == null ? null : originalDatasetDict[id][dataset["yVar"]],
      };
    });

    // filter raw data into a prepared data set
    let prepared = rawData;
    ["N", "O"].forEach((dataType) =>
      dataset.attributeDatatypeList[dataType].forEach((attr) => {
        let filterModel = dataset["attributes"][attr]["filterModel"];
        prepared = prepared.filter((item) => {
          return filterModel.indexOf(item[attr]) !== -1;
        });
      })
    );
    ["Q", "T"].forEach((dataType) =>
      dataset.attributeDatatypeList[dataType].forEach((attr) => {
        let filterModel = dataset["attributes"][attr]["filterModel"];
        prepared = prepared.filter((item) => {
          return (
            parseFloat(item[attr]) >= parseFloat(filterModel[0]) && parseFloat(item[attr]) <= parseFloat(filterModel[1])
          );
        });
      })
    );

    // Create scales and axes if combination is TxQ
    let buckets = [];
    let xAxisTitle = "";
    let yAxisTitle = "";
    let aggTitle =
      dataset["aggType"] == null ? "" : context.userConfig["aggregationMapping"][dataset["aggType"]].toUpperCase();
    let xIsT = context.utilsService.isMeasure(dataset, dataset["xVar"], "T");
    let yIsQ = context.utilsService.isMeasure(dataset, dataset["yVar"], "Q");

    if (xIsT && yIsQ && dataset["aggType"] !== null) {
      // [T x Q] => aggregate yVar grouped by xVar
      context.lineChartConfig.legendGroup.style("display", "block");
      buckets = d3
        .rollups(
          prepared,
          (v) => context.utilsService.aggregate(v, dataset["aggType"], "yVar"),
          (d) => d["xVar"]
        )
        .sort(function (x, y) {
          return d3.ascending(x[0], y[0]); // sort dates
        });
      buckets.forEach((d) => d.push(prepared.filter((obj) => obj["xVar"] == d[0])));
      xAxisTitle = dataset["xVar"];
      yAxisTitle = `${aggTitle}(${dataset["yVar"]})`;
    } else if (xIsT && dataset["yVar"] == null) {
      // [T x NA] => count values in each xVar as aggregate
      context.lineChartConfig.legendGroup.style("display", "block");
      buckets = d3
        .rollups(
          prepared,
          (v) => v.length,
          (d) => d["xVar"]
        )
        .sort(function (x, y) {
          return d3.ascending(x[0], y[0]); // sort dates
        });
      buckets.forEach((d) => d.push(prepared.filter((obj) => obj["xVar"] == d[0])));
      xAxisTitle = dataset["xVar"];
      yAxisTitle = `COUNT(${dataset["xVar"]})`;
    } else {
      // [N/O/Q x N/O/Q/T] OR [T x N/O/T] => unsupported
      context.lineChartConfig.legendGroup.style("display", "none");
      context.lineChartConfig.lineGroup
        .append("text")
        .attr("class", "unsupported-text")
        .attr("transform", `translate(${context.plotWidth / 2},${context.plotHeight / 2})`)
        .attr("text-anchor", "middle")
        .html(context.lineChartConfig.unsupportedMessage);
    }

    // set x scale and axis
    context.lineChartConfig.xScale = d3
      .scaleBand()
      .domain(buckets.map((d) => d[0]))
      .range([0, context.plotWidth])
      .padding(0.1);
    context.lineChartConfig.xAxis = d3.axisBottom(context.lineChartConfig.xScale);

    // Set y scale and axis
    context.lineChartConfig.yScale = d3
      .scaleLinear()
      .domain([0, d3.max(buckets, (d) => d[1])])
      .range([context.plotHeight, 0])
      .nice();
    context.lineChartConfig.yAxis = d3
      .axisLeft(context.lineChartConfig.yScale)
      .tickFormat((d) => context.utilsService.formatLargeNum(+d));

    // draw axes
    context.lineChartConfig.xAxisGroup.call(context.lineChartConfig.xAxis);
    context.lineChartConfig.yAxisGroup.call(context.lineChartConfig.yAxis);

    // draw axis titles
    context.lineChartConfig.xAxisGroup.select(".x.axis.title").remove();
    context.lineChartConfig.xAxisGroup
      .append("g")
      .classed("x axis title", true)
      .attr("opacity", 1)
      .attr("transform", `translate(${context.plotWidth / 2}, 0)`)
      .append("text")
      .attr("text-anchor", "middle")
      .attr("fill", "currentColor")
      .attr("dy", "3.71em")
      .text(xAxisTitle);

    context.lineChartConfig.yAxisGroup.select(".y.axis.title").remove();
    context.lineChartConfig.yAxisGroup
      .append("g")
      .classed("y axis title", true)
      .attr("opacity", 1)
      .attr("transform", `translate(-30, ${context.plotHeight / 2})`)
      .append("text")
      .attr("fill", "currentColor")
      .text(yAxisTitle);

    // prepare data labels for yAxis
    context.lineChartConfig.yAxisGroup
      .selectAll("text")
      .style("text-anchor", "middle")
      .attr("dx", "0.8em")
      .attr("dy", "-1.21em")
      .attr("transform", "rotate(-90)");

    // stagger every other tick label
    context.lineChartConfig.xAxisGroup.selectAll(".tick").each(function (_, i) {
      if (i % 2 !== 0) {
        d3.select(this).select("line").attr("y2", 15);
        d3.select(this).select("text").attr("dy", "1.91em");
      }
    });
    context.lineChartConfig.yAxisGroup.selectAll(".tick").each(function (_, i) {
      if (i % 2 !== 0) {
        d3.select(this).select("line").attr("x2", -15);
        d3.select(this).select("text").attr("dy", "-2.41em");
      }
    });

    // define line creation function
    let line = d3
      .line()
      .curve(d3.curveCardinal)
      .defined((d) => !isNaN(d[1]))
      .x((d) => context.lineChartConfig.xScale(d[0]) + context.lineChartConfig.xScale.bandwidth() / 2)
      .y((d) => context.lineChartConfig.yScale(d[1]));

    // (re-)draw static path connecting line chart points
    context.lineChartConfig.lineGroup.select(".connect-line").datum(buckets).attr("d", line);

    // JOIN data selection using bucket label as key
    let dataBound = context.lineChartConfig.lineGroup.selectAll(".post").data(buckets, (d) => d[0]);

    // DELETE extra points
    dataBound.exit().remove();

    // ENTER new points
    let enterSelection = dataBound.enter().append("g").classed("post", true);

    // UPDATE all existing points
    enterSelection.append("circle");
    enterSelection
      .merge(dataBound)
      .select("circle")
      .attr("transform", (d) => {
        const x = context.lineChartConfig.xScale;
        const y = context.lineChartConfig.yScale;
        d["x"] = x(d[0]) + x.bandwidth() / 2;
        d["y"] = y(d[1]);
        return `translate(${d["x"]}, ${d["y"]})`;
      })
      .attr("r", () => {
        // make the radius a function of the bandwidth for all data, to ensure filter does not change radius
        let fullBandwidth = context.plotWidth / d3.intersection(rawData.map((d) => d["xVar"])).size;
        return `${4 + fullBandwidth / 8}px`;
      })
      .style("fill", (d) => {
        // fill based on interactions with underlying data points!
        
        switch (dataset["colorByMode"]) {
          case "abs":
            const sumInteracted = d[2].reduce(context.utilsService.sumTimesVisited, 0) as number;
            const sumVisits = prepared.reduce(context.utilsService.sumTimesVisited, 0) as number;
            return sumInteracted == 0
              ? "white"
              : context.userConfig.focusSequentialColorScale(sumInteracted / sumVisits);
          case "rel":
            const maxInteracted = d[2].reduce(context.utilsService.maxTimesVisited, 0) as number;
            const maxVisits = prepared.reduce(context.utilsService.maxTimesVisited, 0) as number;
            return maxInteracted == 0
              ? "white"
              : context.userConfig.focusSequentialColorScale(maxInteracted / maxVisits);
          case "binary":
            const visited = d[2].some((el) => el["timesVisited"] > 0);
            return !visited ? "white" : context.userConfig.focusSequentialColorScale(1);
          default:
            return "white";
        }
      })
      .style("fill-opacity", 1)
      .style("stroke", (d) => (d[2].reduce((a, b) => a || b["selected"], false) ? "brown" : "black"))
      .style("stroke-width", (d) => (d[2].reduce((a, b) => a || b["selected"], false) ? "3px" : "1px"))
      .style("stroke-dasharray", (d) => {
        const countSelected = d[2].filter((o) => o["selected"]).length;
        return countSelected < d[2].length && countSelected > 0 ? "3" : "none";
      })
      .style("cursor", "pointer")
      .on("click", function (event, d) {
        if (context.global.appType === "ADMIN") {
          context.utilsService.clickGroup(context, event, {
            aggName: dataset["aggType"] == null ? "count" : dataset["aggType"],
            aggAxis: "y-axis",
            binLabel: d[0],
            binValue: d[1],
            binData: d[2],
          });
        }
      })
      .on("mouseover", function (event, d) {
        context.utilsService.mouseoverGroup(context, event, this, {
          aggName: dataset["aggType"] == null ? "count" : dataset["aggType"],
          aggAxis: "y-axis",
          binLabel: d[0],
          binValue: d[1],
          binData: d[2],
        });
      })
      .on("mouseout", function (event, d) {
        context.utilsService.mouseoutGroup(context, event, {
          aggName: dataset["aggType"] == null ? "count" : dataset["aggType"],
          aggAxis: "y-axis",
          binLabel: d[0],
          binValue: d[1],
          binData: d[2],
        });
      });

    // add event listeners to the line group based on modified buckets
    const lineGroupNode = context.lineChartConfig.lineGroup.node();
    const offset = lineGroupNode.getBoundingClientRect().left;
    const pointsX = buckets.map((d) => d["x"]); // values are always sorted asc
    context.lineChartConfig.lineGroup
      .on("mouseenter", (event) => {
        // only show the intersect line if there's data to display
        if (buckets.length) {
          const x = snapToX(pointsX, event.clientX - offset);
          d3.select(".intersect-line").attr("transform", `translate(${x},0)`).style("display", "block");
        }
      })
      .on("mousemove", (event) => {
        // only move the intersect line if there's data to display
        if (buckets.length) {
          const x = snapToX(pointsX, event.clientX - offset);
          d3.select(".intersect-line").attr("transform", `translate(${x},0)`);
        }
      })
      .on("mouseleave", () => {
        d3.select(".intersect-line").style("display", "none");
      });

    // FILTER can update `buckets` => must update hovered Objects list
    if (dataset["hoveredObjects"]["binName"]) {
      // binName set => there is a bin visible in details view, reset existing object
      let currentBinName = dataset["hoveredObjects"]["binName"];
      let currentBinAttr = dataset["hoveredObjects"]["binAttr"];
      dataset["hoveredObjects"] = { binName: null, binAttr: null, points: {} };
      // look for the bin in the filtered data set. If not there, table is already reset!
      for (let bin of buckets) {
        if (bin[0] == currentBinName && dataset["xVar"] == currentBinAttr) {
          // found the bin! => update hovered Objects for possible FILTER
          dataset["hoveredObjects"]["binName"] = currentBinName;
          dataset["hoveredObjects"]["binAttr"] = currentBinAttr;
          bin[2].forEach((d) => {
            const id = d[dataset["primaryKey"]];
            if (id !== "-") {
              // use dict OBJECT to update source data by reference!
              let dataPoint = originalDatasetDict[id];
              context.utilsService.colorDataPoint(context, dataPoint, bin[2]);
              dataset["hoveredObjects"]["points"][id] = dataPoint;
            }
          });
          // attempt to remove values from the details table
          if (dataset["aggType"] == "min" || dataset["aggType"] == "max") {
            Object.keys(dataset["hoveredObjects"]["points"]).forEach((id) => {
              if (dataset["hoveredObjects"]["points"][id][dataset["yVar"]] !== bin[1]) {
                delete dataset["hoveredObjects"]["points"][id];
              }
            });
          }
          break;
        }
      }
    }
  }
}

/**
 * Returns x coordinate of closest point to the mouse event x coordinate.
 */
function snapToX(pointsX, eventX) {
  let i = 1;
  if (eventX < pointsX[0]) {
    i = 0;
  } else if (eventX > pointsX[pointsX.length - 1]) {
    i = pointsX.length - 1;
  } else {
    while (eventX > pointsX[i]) i++;
    if (pointsX[i] - eventX > eventX - pointsX[i - 1]) i--;
  }
  return pointsX[i];
}
