// libraries
import * as d3 from "d3";
import $ from "jquery";
// local
import { DotPlotConfig } from "src/app/models/viz";
import { sequentialColorRange, SessionPage } from "src/app/models/config";
import { UtilsService } from "src/app/services/utils.service";
import { ChatService } from "src/app/services/socket.service";

export class DotPlot {
  dotPlotConfig;
  plotWidth: number;
  plotHeight: number;
  plotGroup;

  constructor(
    public utilsService: UtilsService,
    public chatService: ChatService,
    public global: SessionPage,
    public userConfig,
    public appConfig
  ) {
    this.dotPlotConfig = new DotPlotConfig();
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
    context.dotPlotConfig.yAxisGroup = context.plotGroup.append("g").classed("y", true).classed("axis", true);
    context.dotPlotConfig.xAxisGroup = context.plotGroup
      .append("g")
      .classed("x", true)
      .classed("axis", true)
      .attr("transform", `translate(${0}, ${context.plotHeight})`);

    // Add dots groups
    context.dotPlotConfig.dotsGroup = context.plotGroup.append("g").classed("dots", true);

    // Add legend group, text and gradient rectangle
    context.dotPlotConfig.legendGroup = context.plotGroup.append("g").classed("legend", true);
    if (true) {
      let xPos = context.plotWidth; // x position of element, gets updated dynamically
      const pad = 5; // padding between elements
      const gradRectWidth = context.plotWidth / 5; // width of gradient rectangle
      const el = context.dotPlotConfig.legendGroup
        .append("text")
        .attr("transform", `translate(${xPos}, ${(-5 / 8) * plotMargins.top})`)
        .attr("text-anchor", "end")
        .text("More Focus");
      xPos -= Math.abs(el.node().getBBox()["x"]) + gradRectWidth + pad;
      context.dotPlotConfig.legendGroup
        .append("rect")
        .attr("transform", `translate(${xPos}, ${(-3 / 4) * plotMargins.top})`)
        .attr("width", gradRectWidth)
        .attr("height", (1 / 8) * plotMargins.top)
        .style("rx", "4")
        .style("fill", "url(#grad)");
      xPos -= pad;
      context.dotPlotConfig.legendGroup
        .append("text")
        .attr("transform", `translate(${xPos}, ${(-5 / 8) * plotMargins.top})`)
        .attr("text-anchor", "end")
        .text("Less Focus");
    }

    // Create unsupported text to display if chart cannot render
    context.dotPlotConfig.unsupportedMessage = `
      <tspan>
        If using numerical
        (<tspan style="font-family: 'Font Awesome 5 Free'; font-weight: 800 !important">&#xf292;</tspan>)
        attributes,
      </tspan>
      <tspan x="0" dy="1.2em">
        you must have
        <tspan style="font-weight: 800 !important">more than one</tspan>!
      </tspan>`;
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
    context.dotPlotConfig.dotsGroup.select(".unsupported-text").remove();

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
    ["T", "Q"].forEach((dataType) =>
      dataset.attributeDatatypeList[dataType].forEach((attr) => {
        let filterModel = dataset["attributes"][attr]["filterModel"];
        prepared = prepared.filter((item) => {
          return (
            parseFloat(item[attr]) >= parseFloat(filterModel[0]) && parseFloat(item[attr]) <= parseFloat(filterModel[1])
          );
        });
      })
    );

    // Create scales and axes based on vis matrix
    let buckets = [];
    let binLabelDelim = " x "; // delimiter for getting axis titles from bin Label
    let xIsNA = dataset["xVar"] == null;
    let yIsNA = dataset["yVar"] == null;
    let xIsQ = context.utilsService.isMeasure(dataset, dataset["xVar"], "Q");
    let yIsQ = context.utilsService.isMeasure(dataset, dataset["yVar"], "Q");

    // initialize x and y scales
    context.dotPlotConfig.xScale = d3.scaleBand().rangeRound([0, context.plotWidth]).padding(0.1);
    context.dotPlotConfig.yScale = d3.scaleBand().rangeRound([context.plotHeight, 0]).padding(0.1);

    if (xIsQ || yIsQ || (xIsNA && yIsNA)) {
      // [Q x any] OR [any x Q] or [NA x NA] => unsupported
      buckets = []; // ensures no points are drawn
      context.dotPlotConfig.xAxisGroup.selectAll("*").remove();
      context.dotPlotConfig.yAxisGroup.selectAll("*").remove();
      context.dotPlotConfig.legendGroup.style("display", "none");
      context.dotPlotConfig.dotsGroup
        .append("text")
        .attr("class", "unsupported-text")
        .attr("transform", `translate(${context.plotWidth / 2},${context.plotHeight / 2})`)
        .attr("text-anchor", "middle")
        .html(context.dotPlotConfig.unsupportedMessage);
    } else {
      // [N/O/T/NA x N/O/T/NA] => supported
      context.dotPlotConfig.legendGroup.style("display", "block");
      if (!xIsNA) {
        // x is N/O/T => horizontal dots
        if (yIsNA) {
          // [N/O/T x NA] => bucket by x only
          buckets = d3.groups(prepared, (d) => `${d["xVar"]}${binLabelDelim}NA`);
        } else {
          // [N/O/T x N/O/T] => bucket by x and y
          buckets = d3.groups(prepared, (d) => `${d["xVar"]}${binLabelDelim}${d["yVar"]}`);
        }
        context.dotPlotConfig.xScale.domain(
          rawData
            .map(function (d) {
              return d["xVar"];
            })
            .sort(function (x, y) {
              return d3.ascending(x, y); // sort domain
            })
        );
        context.dotPlotConfig.xAxis = d3.axisBottom(context.dotPlotConfig.xScale);
        context.dotPlotConfig.xAxisGroup.call(context.dotPlotConfig.xAxis);
        context.dotPlotConfig.xAxisGroup.select(".x.axis.title").remove();
        context.dotPlotConfig.xAxisGroup
          .append("g")
          .classed("x axis title", true)
          .attr("opacity", 1)
          .attr("transform", `translate(${context.plotWidth / 2}, 0)`)
          .append("text")
          .attr("text-anchor", "middle")
          .attr("fill", "currentColor")
          .attr("dy", "3.71em")
          .text(dataset["xVar"]);
      } else {
        // x is NA => remove x axis (if present)
        context.dotPlotConfig.xAxisGroup.selectAll("*").remove();
      }
      if (!yIsNA) {
        // y is N/O/T => vertical dots
        if (xIsNA) {
          // [NA x N/O/T] => bucket y only
          buckets = d3.groups(prepared, (d) => `NA${binLabelDelim}${d["yVar"]}`);
        }
        context.dotPlotConfig.yScale.domain(
          rawData
            .map(function (d) {
              return d["yVar"];
            })
            .sort(function (x, y) {
              return d3.ascending(x, y); // sort domain
            })
        );
        context.dotPlotConfig.yAxis = d3.axisLeft(context.dotPlotConfig.yScale);
        context.dotPlotConfig.yAxisGroup.call(context.dotPlotConfig.yAxis);
        context.dotPlotConfig.yAxisGroup.select(".y.axis.title").remove();
        context.dotPlotConfig.yAxisGroup
          .append("g")
          .classed("y axis title", true)
          .attr("opacity", 1)
          .attr("transform", `translate(-30, ${context.plotHeight / 2})`)
          .append("text")
          .attr("fill", "currentColor")
          .text(dataset["yVar"]);
        context.dotPlotConfig.yAxisGroup
          .selectAll("text")
          .style("text-anchor", "middle")
          .attr("dx", "0.8em")
          .attr("dy", "-1.21em")
          .attr("transform", "rotate(-90)");
      } else {
        // y is NA => remove y axis (if present)
        context.dotPlotConfig.yAxisGroup.selectAll("*").remove();
      }
    }

    // stagger every other tick label
    context.dotPlotConfig.xAxisGroup.selectAll(".tick").each(function (_, i) {
      if (i % 2 !== 0) {
        d3.select(this).select("line").attr("y2", 15);
        d3.select(this).select("text").attr("dy", "1.91em");
      }
    });
    context.dotPlotConfig.yAxisGroup.selectAll(".tick").each(function (_, i) {
      if (i % 2 !== 0) {
        d3.select(this).select("line").attr("x2", -15);
        d3.select(this).select("text").attr("dy", "-2.41em");
      }
    });

    // JOIN data selection using bucket label as key
    let dataBound = context.dotPlotConfig.dotsGroup.selectAll(".post").data(buckets, (d) => d[0]);

    // DELETE extra dots (fade them out)
    dataBound.exit().remove();

    // ENTER new dots (starting invisible, later fade them in)
    let enterSelection = dataBound.enter().append("g").classed("post", true);

    // UPDATE all existing dots
    enterSelection.append("circle");
    enterSelection
      .merge(dataBound)
      .select("circle")
      .attr("transform", (d) => {
        const x = context.dotPlotConfig.xScale;
        const y = context.dotPlotConfig.yScale;
        d["x"] =
          !xIsQ && !xIsNA
            ? x(d[0].split(binLabelDelim)[0]) + x.bandwidth() / 2 // dist horizontal
            : 0.5 * y.bandwidth(); // align left
        d["y"] =
          !yIsQ && !yIsNA
            ? y(d[0].split(binLabelDelim)[1]) + y.bandwidth() / 2 // dist vertical
            : context.plotHeight - 0.5 * x.bandwidth(); // align bottom
        return `translate(${d["x"]}, ${d["y"]})`;
      })
      .attr("r", () => {
        // fit the dots within the smallest bandwidth on either axis
        const x = context.dotPlotConfig.xScale;
        const y = context.dotPlotConfig.yScale;
        return `${0.25 * Math.min(x.bandwidth(), y.bandwidth())}px`;
      })
      .style("fill", (d) => {
        // fill based on interactions with underlying data points!
        
        switch (dataset["colorByMode"]) {
          case "abs":
            const sumInteracted = d[1].reduce(context.utilsService.sumTimesVisited, 0) as number;
            const sumVisits = prepared.reduce(context.utilsService.sumTimesVisited, 0) as number;
            return sumInteracted == 0
              ? "white"
              : context.userConfig.focusSequentialColorScale(sumInteracted / sumVisits);
          case "rel":
            const maxInteracted = d[1].reduce(context.utilsService.maxTimesVisited, 0) as number;
            const maxVisits = prepared.reduce(context.utilsService.maxTimesVisited, 0) as number;
            return maxInteracted == 0
              ? "white"
              : context.userConfig.focusSequentialColorScale(maxInteracted / maxVisits);
          case "binary":
            const visited = d[1].some((el) => el["timesVisited"] > 0);
            return !visited ? "white" : context.userConfig.focusSequentialColorScale(1);
          default:
            return "white";
        }
      })
      .style("fill-opacity", 0.8)
      .style("stroke", (d) => (d[1].reduce((a, b) => a || b["selected"], false) ? "brown" : "black"))
      .style("stroke-width", (d) => (d[1].reduce((a, b) => a || b["selected"], false) ? "3px" : "1px"))
      .style("stroke-dasharray", (d) => {
        const countSelected = d[1].filter((o) => o["selected"]).length;
        return countSelected < d[1].length && countSelected > 0 ? "3" : "none";
      })
      .style("cursor", "pointer")
      .on("click", function (event, d) {
        if (context.global.appType === "ADMIN") {
          context.utilsService.clickGroup(context, event, {
            aggName: null,
            aggAxis: null,
            binLabel: d[0],
            binValue: null,
            binData: d[1],
          });
        }
      })
      .on("mouseover", function (event, d) {
        context.utilsService.mouseoverGroup(context, event, this, {
          aggName: null,
          aggAxis: null,
          binLabel: d[0],
          binValue: null,
          binData: d[1],
        });
      })
      .on("mouseout", function (event, d) {
        context.utilsService.mouseoutGroup(context, event, {
          aggName: null,
          aggAxis: null,
          binLabel: d[0],
          binValue: null,
          binData: d[1],
        });
      });

    // FILTER can update `buckets` => must update hovered Objects list
    if (dataset["hoveredObjects"]["binName"]) {
      // binName set => there is a bin visible in details view, reset existing object
      let currentBinName = dataset["hoveredObjects"]["binName"];
      dataset["hoveredObjects"] = { binName: null, binAttr: null, points: {} };
      // look for the bin in the filtered data set. If not there, table is already reset!
      for (let bin of buckets) {
        if (bin[0] == currentBinName) {
          // found the bin! => update hovered Objects for possible FILTER
          dataset["hoveredObjects"]["binName"] = currentBinName;
          bin[1].forEach((d) => {
            const id = d[dataset["primaryKey"]];
            if (id !== "-") {
              // use dict OBJECT to update source data by reference!
              let dataPoint = originalDatasetDict[id];
              context.utilsService.colorDataPoint(context, dataPoint, bin[1]);
              dataset["hoveredObjects"]["points"][id] = dataPoint;
            }
          });
          break;
        }
      }
    }
  }
}
