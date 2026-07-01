// libraries
import * as d3 from "d3";
import $ from "jquery";
// local
import { BarChartConfig } from "src/app/models/viz";
import { sequentialColorRange, SessionPage } from "src/app/models/config";
import { UtilsService } from "src/app/services/utils.service";
import { ChatService } from "src/app/services/socket.service";

export class BarChart {
  barChartConfig;
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
    this.barChartConfig = new BarChartConfig();
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
      .attr("transform", `translate(${plotMargins.left},${plotMargins.top})`);

    // Add X and Y axis groups
    context.barChartConfig.yAxisGroup = context.plotGroup.append("g").classed("y", true).classed("axis", true);
    context.barChartConfig.xAxisGroup = context.plotGroup
      .append("g")
      .classed("x", true)
      .classed("axis", true)
      .attr("transform", `translate(${0},${context.plotHeight})`);

    // Add bar groups
    context.barChartConfig.barsGroup = context.plotGroup.append("g").classed("bars", true);

    // Add legend group, text and gradient rectangle
    context.barChartConfig.legendGroup = context.plotGroup.append("g").classed("legend", true);
    if (true) {
      let xPos = context.plotWidth; // x position of element, gets updated dynamically
      const pad = 5; // padding between elements
      const gradRectWidth = context.plotWidth / 5; // width of gradient rectangle
      const el = context.barChartConfig.legendGroup
        .append("text")
        .attr("transform", `translate(${xPos}, ${(-5 / 8) * plotMargins.top})`)
        .attr("text-anchor", "end")
        .text("More Focus");
      xPos -= Math.abs(el.node().getBBox()["x"]) + gradRectWidth + pad;
      context.barChartConfig.legendGroup
        .append("rect")
        .attr("transform", `translate(${xPos}, ${(-3 / 4) * plotMargins.top})`)
        .attr("width", gradRectWidth)
        .attr("height", (1 / 8) * plotMargins.top)
        .style("rx", "4")
        .style("fill", "url(#grad)");
      xPos -= pad;
      context.barChartConfig.legendGroup
        .append("text")
        .attr("transform", `translate(${xPos}, ${(-5 / 8) * plotMargins.top})`)
        .attr("text-anchor", "end")
        .text("Less Focus");
    }

    // Create unsupported text to display if chart cannot render
    context.barChartConfig.unsupportedMessage = `
      <tspan>If using
        categorical (<tspan style="font-family: 'Font Awesome 5 Free'; font-weight: 800 !important">&#xf031;</tspan>)
        and/or
        temporal (<tspan style="font-family: 'Font Awesome 5 Free'; font-weight: 800 !important">&#xf133;</tspan>)
      </tspan>
      <tspan x="0" dy="1.2em">
        attributes, you must have 
        <tspan style="font-weight: 800 !important">only one</tspan>!
      </tspan>`;
  }

  /**
   * Calculate new values and re-draw plot.
   */
  update() {
    let context = this;
    let utils = context.utilsService;
    let originalDatasetDict = context.userConfig["originalDatasetDict"];
    let dataset = context.appConfig[context.global.appMode];

    // if there's no dataset don't update the bar chart
    if (!originalDatasetDict) return;

    // Clear unsupported message
    context.barChartConfig.barsGroup.select(".unsupported-text").remove();

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

    // Create buckets, scales and axes based on xy data types
    let buckets = []; // list of label-value pairs: [[label, value], ...]
    let horizontal = false;
    let xAxisTitle = "";
    let yAxisTitle = "";
    let aggTitle =
      dataset["aggType"] == null ? "" : context.userConfig["aggregationMapping"][dataset["aggType"]].toUpperCase();
    let xScale = context.barChartConfig.xScale;
    let xAxis = context.barChartConfig.xAxis;
    let yScale = context.barChartConfig.yScale;
    let yAxis = context.barChartConfig.yAxis;
    let xIsQ = utils.isMeasure(dataset, dataset["xVar"], "Q");
    let yIsQ = utils.isMeasure(dataset, dataset["yVar"], "Q");

    if (dataset["yVar"] == null) {
      // yVar is NA => Vertical histogram
      xScale = d3.scaleBand().range([0, context.plotWidth]).padding(0.1);
      xAxis = d3.axisBottom(xScale);
      if (xIsQ) {
        // [Q x NA] => Vertical binned histogram of count
        context.barChartConfig.legendGroup.style("display", "block");
        const bins = d3.bin().value((d) => d["xVar"])(prepared);
        buckets = bins.map((bin) => {
          const lb = utils.formatLargeNum(+bin.x0); // lowerbound
          const ub = utils.formatLargeNum(+bin.x1); // upperbound
          const val = utils.aggregate(bin, "count", "xVar");
          return [`[${lb}, ${ub})`, val, bin];
        });
        xAxis.tickFormat((_, i) => buckets[i][0]);
        xAxisTitle = dataset["xVar"];
        yAxisTitle = `COUNT(${dataset["xVar"]})`;
      } else if (dataset["xVar"] !== null) {
        // [N/O/T x NA] => Vertical histogram of count
        context.barChartConfig.legendGroup.style("display", "block");
        buckets = d3
          .rollups(
            prepared,
            (v) => v.length,
            (d) => d["xVar"]
          )
          .sort(function (x, y) {
            return d3.ascending(x[0], y[0]); // sort buckets
          });
        buckets.forEach((d) => d.push(prepared.filter((obj) => obj["xVar"] == d[0])));
        xAxis.tickFormat((_, i) => `${buckets[i][0]}`);
        xAxisTitle = dataset["xVar"];
        yAxisTitle = `COUNT(${dataset["xVar"]})`;
      } else {
        // [NA x NA] => unsupported
        context.barChartConfig.legendGroup.style("display", "none");
        context.barChartConfig.barsGroup
          .append("text")
          .attr("class", "unsupported-text")
          .attr("transform", `translate(${context.plotWidth / 2},${context.plotHeight / 2})`)
          .attr("text-anchor", "middle")
          .html(context.barChartConfig.unsupportedMessage);
      }
      xScale.domain(d3.range(buckets.length));
      yScale = d3.scaleLinear().range([context.plotHeight, 0]);
      yScale.domain([0, d3.max(buckets, (d) => d[1])]).nice();
      yAxis = d3.axisLeft(yScale).tickFormat((d) => utils.formatLargeNum(+d));
    } else if (dataset["xVar"] == null) {
      // xVar is NA => Horizontal histogram
      horizontal = true;
      yScale = d3.scaleBand().range([0, context.plotHeight]).padding(0.1);
      yAxis = d3.axisLeft(yScale);
      if (yIsQ) {
        // [NA x Q] => Horizontal binned histogram of count
        context.barChartConfig.legendGroup.style("display", "block");
        const bins = d3.bin().value((d) => d["yVar"])(prepared);
        buckets = bins
          .map((bin) => {
            const lb = utils.formatLargeNum(+bin.x0); // lowerbound
            const ub = utils.formatLargeNum(+bin.x1); // upperbound
            const val = utils.aggregate(bin, "count", "yVar");
            return [`[${lb}, ${ub})`, val, bin];
          })
          .reverse(); // sort buckets reverse vertically
        yAxis.tickFormat((_, i) => buckets[i][0]);
        yAxisTitle = dataset["yVar"];
        xAxisTitle = `COUNT(${dataset["yVar"]})`;
      } else if (dataset["yVar"] !== null) {
        // [NA x N/O/T] => Horizontal histogram of count
        context.barChartConfig.legendGroup.style("display", "block");
        buckets = d3
          .rollups(
            prepared,
            (v) => v.length,
            (d) => d["yVar"]
          )
          .sort(function (x, y) {
            return d3.ascending(y[0], x[0]); // sort buckets reverse vertically
          });
        buckets.forEach((d) => d.push(prepared.filter((obj) => obj["yVar"] == d[0])));
        yAxis.tickFormat((_, i) => `${buckets[i][0]}`);
        yAxisTitle = dataset["yVar"];
        xAxisTitle = `COUNT(${dataset["yVar"]})`;
      } else {
        // [NA x NA] => unsupported
        context.barChartConfig.legendGroup.style("display", "none");
        context.barChartConfig.barsGroup
          .append("text")
          .attr("class", "unsupported-text")
          .attr("transform", `translate(${context.plotWidth / 2},${context.plotHeight / 2})`)
          .attr("text-anchor", "middle")
          .html(context.barChartConfig.unsupportedMessage);
      }
      yScale.domain(d3.range(buckets.length));
      xScale = d3.scaleLinear().range([0, context.plotWidth]);
      xScale.domain([0, d3.max(buckets, (d) => d[1])]).nice();
      xAxis = d3.axisBottom(xScale).tickFormat((d) => utils.formatLargeNum(+d));
    } else {
      // both xVar and yVar are defined
      if (yIsQ) {
        // yVar is Q => vertical bar chart
        xScale = d3.scaleBand().range([0, context.plotWidth]).padding(0.1);
        xAxis = d3.axisBottom(xScale);
        xAxisTitle = dataset["xVar"];
        yAxisTitle = `${aggTitle}(${dataset["yVar"]})`;
        if (xIsQ) {
          // [Q x Q] => bin x, rollup, aggregate y
          context.barChartConfig.legendGroup.style("display", "block");
          const bins = d3.bin().value((d) => d["xVar"])(prepared);
          buckets = bins.map((bin) => {
            const lb = utils.formatLargeNum(+bin.x0); // lowerbound
            const ub = utils.formatLargeNum(+bin.x1); // upperbound
            const val = utils.aggregate(bin, dataset["aggType"], "yVar");
            return [`[${lb}, ${ub})`, val, bin];
          });
          xAxis.tickFormat((_, i) => buckets[i][0]);
        } else {
          // [N/O/T x Q] => rollup, aggregate
          context.barChartConfig.legendGroup.style("display", "block");
          buckets = d3
            .rollups(
              prepared,
              (v) => utils.aggregate(v, dataset["aggType"], "yVar"),
              (d) => d["xVar"]
            )
            .sort(function (x, y) {
              return d3.ascending(x[0], y[0]); // sort buckets
            });
          buckets.forEach((d) => d.push(prepared.filter((obj) => obj["xVar"] == d[0])));
          xAxis.tickFormat((i) => `${buckets[i][0]}`);
        }
        xScale.domain(d3.range(buckets.length));
        yScale = d3.scaleLinear().range([context.plotHeight, 0]);
        yScale.domain([0, d3.max(buckets, (d) => d[1])]).nice();
        yAxis = d3.axisLeft(yScale).tickFormat((d) => utils.formatLargeNum(+d));
      } else {
        // yVar is N/O/T => horizontal bar chart
        horizontal = true;
        yScale = d3.scaleBand().range([0, context.plotHeight]).padding(0.1);
        yAxis = d3.axisLeft(yScale);
        if (xIsQ) {
          // [Q x N/O/T] => rollup, aggregate => horizontal bar chart
          context.barChartConfig.legendGroup.style("display", "block");
          buckets = d3
            .rollups(
              prepared,
              (v) => utils.aggregate(v, dataset["aggType"], "xVar"),
              (d) => d["yVar"]
            )
            .sort(function (x, y) {
              return d3.ascending(y[0], x[0]); // sort buckets reverse vertically
            });
          buckets.forEach((d) => d.push(prepared.filter((obj) => obj["yVar"] == d[0])));
          yAxis.tickFormat((i) => `${buckets[i][0]}`);
          yAxisTitle = dataset["yVar"];
          xAxisTitle = `${aggTitle}(${dataset["xVar"]})`;
        } else {
          // [N/O/T x N/O/T] => unsupported
          context.barChartConfig.legendGroup.style("display", "none");
          context.barChartConfig.barsGroup
            .append("text")
            .attr("class", "unsupported-text")
            .attr("transform", `translate(${context.plotWidth / 2},${context.plotHeight / 2})`)
            .attr("text-anchor", "middle")
            .html(context.barChartConfig.unsupportedMessage);
        }
        yScale.domain(d3.range(buckets.length));
        xScale = d3.scaleLinear().range([0, context.plotWidth]);
        xScale.domain([0, d3.max(buckets, (d) => d[1])]).nice();
        xAxis = d3.axisBottom(xScale).tickFormat((d) => utils.formatLargeNum(+d));
      }
    }

    // draw axes
    context.barChartConfig.xAxisGroup.call(xAxis);
    context.barChartConfig.yAxisGroup.call(yAxis);

    // draw axis titles
    context.barChartConfig.xAxisGroup.select(".x.axis.title").remove();
    context.barChartConfig.xAxisGroup
      .append("g")
      .classed("x axis title", true)
      .attr("opacity", 1)
      .attr("transform", `translate(${context.plotWidth / 2}, 0)`)
      .append("text")
      .attr("text-anchor", "middle")
      .attr("fill", "currentColor")
      .attr("dy", "3.71em")
      .text(xAxisTitle);
    context.barChartConfig.yAxisGroup.select(".y.axis.title").remove();
    context.barChartConfig.yAxisGroup
      .append("g")
      .classed("y axis title", true)
      .attr("opacity", 1)
      .attr("transform", `translate(-30, ${context.plotHeight / 2})`)
      .append("text")
      .attr("fill", "currentColor")
      .text(yAxisTitle);

    // prepare data labels for yAxis
    context.barChartConfig.yAxisGroup
      .selectAll("text")
      .style("text-anchor", "middle")
      .attr("dx", "0.8em")
      .attr("dy", "-1.21em")
      .attr("transform", "rotate(-90)");

    // stagger every other tick label
    context.barChartConfig.xAxisGroup.selectAll(".tick").each(function (_, i) {
      if (i % 2 !== 0) {
        d3.select(this).select("line").attr("y2", 15);
        d3.select(this).select("text").attr("dy", "1.91em");
      }
    });
    context.barChartConfig.yAxisGroup.selectAll(".tick").each(function (_, i) {
      if (i % 2 !== 0) {
        d3.select(this).select("line").attr("x2", -15);
        d3.select(this).select("text").attr("dy", "-2.41em");
      }
    });

    // Store updated scales and axes back in the chart config
    context.barChartConfig.xScale = xScale;
    context.barChartConfig.yScale = yScale;
    context.barChartConfig.xAxis = xAxis;
    context.barChartConfig.yAxis = yAxis;

    // REMOVE all bar groups first!
    context.barChartConfig.barsGroup.selectAll(".post").remove();

    // JOIN data selection using bucket label as key
    let dataBound = context.barChartConfig.barsGroup.selectAll(".post").data(buckets, (d) => `${d[0]}`);

    // ENTER new group for each bar and text label
    let enterSelection = dataBound.enter().append("g").classed("post", true);

    // ENTER text for all bars
    const offset = 5;
    enterSelection
      .append("text")
      .attr("transform", (d, i) => {
        const x = horizontal ? xScale(0) + xScale(d[1]) + offset : xScale(i) + xScale.bandwidth() / 2;
        const y = horizontal ? yScale(i) + yScale.bandwidth() / 2 + 4 : yScale(d[1]) - offset;
        return `translate(${x},${y})`;
      })
      .attr("display", "none")
      .style("text-anchor", () => (horizontal ? "start" : "middle"))
      .text((d) => utils.formatLargeNum(+d[1]));

    // ENTER all bars
    enterSelection
      .append("rect")
      .attr("transform", (d, i) => {
        if (horizontal) {
          d["x"] = xScale(0);
          d["y"] = yScale(i);
        } else {
          d["x"] = xScale(i);
          d["y"] = yScale(d[1]);
        }
        return `translate(${d["x"]},${d["y"]})`;
      })
      .attr("height", (d) => (horizontal ? yScale.bandwidth() : yScale(0) - yScale(d[1])))
      .attr("width", (d) => (horizontal ? xScale(d[1]) - xScale(0) : xScale.bandwidth()))
      .style("fill", (d) => {
        // fill based on interactions with underlying data points!
        
        switch (dataset["colorByMode"]) {
          case "abs":
            const sumInteracted = d[2].reduce(utils.sumTimesVisited, 0) as number;
            const sumVisits = prepared.reduce(utils.sumTimesVisited, 0) as number;
            return sumInteracted == 0
              ? "white"
              : context.userConfig.focusSequentialColorScale(sumInteracted / sumVisits);
          case "rel":
            const maxInteracted = d[2].reduce(utils.maxTimesVisited, 0) as number;
            const maxVisits = prepared.reduce(utils.maxTimesVisited, 0) as number;
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
      .style("fill-opacity", 0.8)
      .style("stroke", (d) => (d[2].reduce((a, b) => a || b["selected"], false) ? "brown" : "black"))
      .style("stroke-width", (d) => (d[2].reduce((a, b) => a || b["selected"], false) ? "3px" : "1px"))
      .style("stroke-dasharray", (d) => {
        const countSelected = d[2].filter((o) => o["selected"]).length;
        return countSelected < d[2].length && countSelected > 0 ? "4" : "none";
      })
      .style("cursor", "pointer")
      .on("click", function (event, d) {
        if (context.global.appType === "ADMIN") {
          utils.clickGroup(context, event, {
            aggName: dataset["aggType"] == null ? "count" : dataset["aggType"],
            aggAxis: horizontal ? "x-axis" : "y-axis",
            binLabel: d[0],
            binValue: d[1],
            binData: d[2],
          });
        }
      })
      .on("mouseover", function (event, d) {
        d3.select(this.parentNode).select("text").attr("display", "block");
        utils.mouseoverGroup(context, event, this, {
          aggName: dataset["aggType"] == null ? "count" : dataset["aggType"],
          aggAxis: horizontal ? "x-axis" : "y-axis",
          binLabel: d[0],
          binValue: d[1],
          binData: d[2],
        });
      })
      .on("mouseout", function (event, d) {
        d3.select(this.parentNode).select("text").attr("display", "none");
        utils.mouseoutGroup(context, event, {
          aggName: dataset["aggType"] == null ? "NA" : dataset["aggType"],
          aggAxis: horizontal ? "x-axis" : "y-axis",
          binLabel: d[0],
          binValue: d[1],
          binData: d[2],
        });
      });

    // FILTER can update `buckets` => must update hovered Objects list
    if (dataset["hoveredObjects"]["binName"]) {
      // binName set => there is a bin visible in details view, reset existing object
      let currentBinName = dataset["hoveredObjects"]["binName"];
      let currentBinAttr = dataset["hoveredObjects"]["binAttr"];
      dataset["hoveredObjects"] = { binName: null, binAttr: null, points: {} };
      // look for the bin in the filtered data set. If not there, table is already reset!
      for (let bin of buckets) {
        if (
          bin[0] == currentBinName &&
          ((horizontal && dataset["yVar"] == currentBinAttr) || (!horizontal && dataset["xVar"] == currentBinAttr))
        ) {
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
            if (horizontal) {
              Object.keys(dataset["hoveredObjects"]["points"]).forEach((id) => {
                if (dataset["hoveredObjects"]["points"][id][dataset["xVar"]] !== bin[1]) {
                  delete dataset["hoveredObjects"]["points"][id];
                }
              });
            } else {
              Object.keys(dataset["hoveredObjects"]["points"]).forEach((id) => {
                if (dataset["hoveredObjects"]["points"][id][dataset["yVar"]] !== bin[1]) {
                  delete dataset["hoveredObjects"]["points"][id];
                }
              });
            }
          }
          break;
        }
      }
    }
  }
}
