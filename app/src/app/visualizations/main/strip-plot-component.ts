// libraries
import * as d3 from "d3";
import $ from "jquery";
// local
import { StripPlotConfig } from "src/app/models/viz";
import { sequentialColorRange, SessionPage } from "src/app/models/config";
import { UtilsService } from "src/app/services/utils.service";
import { ChatService } from "src/app/services/socket.service";

export class StripPlot {
  stripPlotConfig;
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
    this.stripPlotConfig = new StripPlotConfig();
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
    context.stripPlotConfig.yAxisGroup = context.plotGroup.append("g").classed("y", true).classed("axis", true);
    context.stripPlotConfig.xAxisGroup = context.plotGroup
      .append("g")
      .classed("x", true)
      .classed("axis", true)
      .attr("transform", `translate(${0}, ${context.plotHeight})`);

    // Add strips groups
    context.stripPlotConfig.stripsGroup = context.plotGroup.append("g").classed("strips", true);

    // Add legend group, text and gradient rectangle
    context.stripPlotConfig.legendGroup = context.plotGroup.append("g").classed("legend", true);
    if (true) {
      const pad = 5; // padding (px) between elements
      const gradRectWidth = context.plotWidth / 5; // width of gradient rectangle
      const leftLabel = "Less Focus"; // label on the left of the legend gradient
      const rightLabel = "More Focus"; // label on the right of the legend gradient
      // build the legend right to left
      let xPos = context.plotWidth; // x position of element, gets updated dynamically
      let el = context.stripPlotConfig.legendGroup
        .append("text")
        .attr("transform", `translate(${xPos}, ${(-5 / 8) * plotMargins.top})`)
        .attr("text-anchor", "end")
        .text(rightLabel);
      xPos -= Math.abs(el.node().getBBox()["x"]) + gradRectWidth + pad;
      context.stripPlotConfig.legendGroup
        .append("rect")
        .attr("transform", `translate(${xPos}, ${(-3 / 4) * plotMargins.top})`)
        .attr("width", gradRectWidth)
        .attr("height", (1 / 8) * plotMargins.top)
        .style("rx", "4")
        .style("fill", "url(#grad)");
      xPos -= pad;
      context.stripPlotConfig.legendGroup
        .append("text")
        .attr("transform", `translate(${xPos}, ${(-5 / 8) * plotMargins.top})`)
        .attr("text-anchor", "end")
        .text(leftLabel);
    }

    // Create unsupported text to display if chart cannot render
    context.stripPlotConfig.unsupportedMessage = `
      <tspan>
        Try using one numerical
        (<tspan style="font-family: 'Font Awesome 5 Free'; font-weight: 800 !important">&#xf292;</tspan>)
      </tspan>
      <tspan x="0" dy="1.2em">
        attribute
        <tspan style="font-weight: 800 !important">at most</tspan> 
        on either axis!
      </tspan>`;
  }

  /**
   * Calculate new values and re-draw plot.
   */
  update() {
    let context = this;
    let originalDatasetDict = context.userConfig["originalDatasetDict"];
    let dataset = context.appConfig[context.global.appMode];

    // if there's no dataset don't update the chart
    if (!originalDatasetDict) return;

    // Clear unsupported message
    context.stripPlotConfig.stripsGroup.select(".unsupported-text").remove();

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
        prepared = prepared.filter(
          (item) =>
            parseFloat(item[attr]) >= parseFloat(filterModel[0]) && parseFloat(item[attr]) <= parseFloat(filterModel[1])
        );
      })
    );

    // Create scales and axes based on vis matrix
    let yAxisMajor = false;
    let xAxisCategorical = false;
    let yAxisCategorical = false;
    let xIsQ = context.utilsService.isMeasure(dataset, dataset["xVar"], "Q");
    let yIsQ = context.utilsService.isMeasure(dataset, dataset["yVar"], "Q");

    if (xIsQ && yIsQ) {
      // [Q x Q] => unsupported, remove x axis as well
      prepared = []; // ensures no points are drawn
      context.stripPlotConfig.xAxisGroup.selectAll("*").remove();
      context.stripPlotConfig.yAxisGroup.selectAll("*").remove();
      context.stripPlotConfig.legendGroup.style("display", "none");
      context.stripPlotConfig.stripsGroup
        .append("text")
        .attr("class", "unsupported-text")
        .attr("transform", `translate(${context.plotWidth / 2},${context.plotHeight / 2})`)
        .attr("text-anchor", "middle")
        .html(context.stripPlotConfig.unsupportedMessage);
    } else if (xIsQ) {
      // x is Q => vertical strips
      context.stripPlotConfig.legendGroup.style("display", "block");
      context.stripPlotConfig.yAxisGroup.selectAll("*").remove();
      context.stripPlotConfig.xScale = d3.scaleLinear().range([0, context.plotWidth]);
      if (!yIsQ && dataset["yVar"] !== null) {
        // [Q x N/O/T] => vertical strips with categorical y axis
        yAxisCategorical = true;
        context.stripPlotConfig.yScale = d3
          .scaleBand()
          .domain(
            rawData
              .map(function (d) {
                return d["yVar"];
              })
              .sort(function (x, y) {
                return d3.ascending(x, y); // sort domain
              })
          )
          .rangeRound([context.plotHeight, 0])
          .padding(0.1);
        context.stripPlotConfig.yAxis = d3.axisLeft(context.stripPlotConfig.yScale);
        context.stripPlotConfig.yAxisGroup.call(context.stripPlotConfig.yAxis);
        context.stripPlotConfig.yAxisGroup.select(".y.axis.title").remove();
        context.stripPlotConfig.yAxisGroup
          .append("g")
          .classed("y axis title", true)
          .attr("opacity", 1)
          .attr("transform", `translate(-30, ${context.plotHeight / 2})`)
          .append("text")
          .attr("fill", "currentColor")
          .text(dataset["yVar"]);
        context.stripPlotConfig.yAxisGroup
          .selectAll("text")
          .style("text-anchor", "middle")
          .attr("dx", "0.8em")
          .attr("dy", "-1.21em")
          .attr("transform", "rotate(-90)");
      }
      context.stripPlotConfig.xScale.domain(
        d3.extent(rawData, function (d) {
          return d["xVar"];
        })
      );
      context.stripPlotConfig.xAxis = d3
        .axisBottom(context.stripPlotConfig.xScale)
        .tickFormat((d) => context.utilsService.formatLargeNum(+d));
      context.stripPlotConfig.xAxisGroup.call(context.stripPlotConfig.xAxis);
      context.stripPlotConfig.xAxisGroup.select(".x.axis.title").remove();
      context.stripPlotConfig.xAxisGroup
        .append("g")
        .classed("x axis title", true)
        .attr("opacity", 1)
        .attr("transform", `translate(${context.plotWidth / 2}, 0)`)
        .append("text")
        .attr("text-anchor", "middle")
        .attr("fill", "currentColor")
        .attr("dy", "3.71em")
        .text(dataset["xVar"]);
    } else if (yIsQ) {
      // y is Q => horizontal strips
      yAxisMajor = true;
      context.stripPlotConfig.legendGroup.style("display", "block");
      context.stripPlotConfig.xAxisGroup.selectAll("*").remove();
      context.stripPlotConfig.yScale = d3.scaleLinear().range([context.plotHeight, 0]);
      if (!xIsQ && dataset["xVar"] !== null) {
        // [N/O/T x Q] => horizontal strips with categorical x axis
        xAxisCategorical = true;
        context.stripPlotConfig.xScale = d3
          .scaleBand()
          .domain(
            rawData
              .map(function (d) {
                return d["xVar"];
              })
              .sort(function (x, y) {
                return d3.ascending(x, y); // sort domain
              })
          )
          .rangeRound([0, context.plotWidth])
          .padding(0.1);
        context.stripPlotConfig.xAxis = d3.axisBottom(context.stripPlotConfig.xScale);
        context.stripPlotConfig.xAxisGroup.call(context.stripPlotConfig.xAxis);
        context.stripPlotConfig.xAxisGroup.select(".x.axis.title").remove();
        context.stripPlotConfig.xAxisGroup
          .append("g")
          .classed("x axis title", true)
          .attr("opacity", 1)
          .attr("transform", `translate(${context.plotWidth / 2}, 0)`)
          .append("text")
          .attr("text-anchor", "middle")
          .attr("fill", "currentColor")
          .attr("dy", "3.71em")
          .text(dataset["xVar"]);
      }
      context.stripPlotConfig.yScale.domain(
        d3.extent(rawData, function (d) {
          return d["yVar"];
        })
      );
      context.stripPlotConfig.yAxis = d3
        .axisLeft(context.stripPlotConfig.yScale)
        .tickFormat((d) => context.utilsService.formatLargeNum(+d));
      context.stripPlotConfig.yAxisGroup.call(context.stripPlotConfig.yAxis);
      context.stripPlotConfig.yAxisGroup.select(".y.axis.title").remove();
      context.stripPlotConfig.yAxisGroup
        .append("g")
        .classed("y axis title", true)
        .attr("opacity", 1)
        .attr("transform", `translate(-30, ${context.plotHeight / 2})`)
        .append("text")
        .attr("fill", "currentColor")
        .text(dataset["yVar"]);
      context.stripPlotConfig.yAxisGroup
        .selectAll("text")
        .style("text-anchor", "middle")
        .attr("dx", "0.8em")
        .attr("dy", "-1.21em")
        .attr("transform", "rotate(-90)");
    } else {
      // [N/T/O x NA] OR [NA x N/T/O] OR [N/T/O/Q x N/T/O/Q] => unsupported
      prepared = []; // ensures no points are drawn
      context.stripPlotConfig.xAxisGroup.selectAll("*").remove();
      context.stripPlotConfig.yAxisGroup.selectAll("*").remove();
      context.stripPlotConfig.legendGroup.style("display", "none");
      context.stripPlotConfig.stripsGroup
        .append("text")
        .attr("class", "unsupported-text")
        .attr("transform", `translate(${context.plotWidth / 2},${context.plotHeight / 2})`)
        .attr("text-anchor", "middle")
        .html(context.stripPlotConfig.unsupportedMessage);
    }

    // draw gridlines along the major axis
    if (yAxisMajor && prepared.length) {
      // y axis gridlines => remove existing and re-draw
      context.stripPlotConfig.yAxisGroup.selectAll(".gridline").remove();
      let linesBound = context.stripPlotConfig.yAxisGroup
        .selectAll(".gridline")
        .data(context.stripPlotConfig.yScale.ticks());
      let enterLines = linesBound
        .enter()
        .append("g")
        .classed("gridline", true)
        .attr("opacity", 0.15)
        .attr("transform", (d) => `translate(0,${context.stripPlotConfig.yScale(d)})`);
      enterLines.append("line");
      enterLines
        .merge(linesBound)
        .select("line")
        .attr("x2", () => (xAxisCategorical ? context.plotWidth : 0.1 * context.plotWidth))
        .attr("stroke", "currentColor");
    } else if (prepared.length) {
      // x axis gridlines => remove existing and re-draw
      context.stripPlotConfig.xAxisGroup.selectAll(".gridline").remove();
      let linesBound = context.stripPlotConfig.xAxisGroup
        .selectAll(".gridline")
        .data(context.stripPlotConfig.xScale.ticks());
      let enterLines = linesBound
        .enter()
        .append("g")
        .classed("gridline", true)
        .attr("opacity", 0.15)
        .attr("transform", (d) => `translate(${context.stripPlotConfig.xScale(d)},0)`);
      enterLines.append("line");
      enterLines
        .merge(linesBound)
        .select("line")
        .attr("y2", () => (yAxisCategorical ? -context.plotHeight : -0.1 * context.plotHeight))
        .attr("stroke", "currentColor");
    }

    // stagger every other tick label
    context.stripPlotConfig.xAxisGroup.selectAll(".tick").each(function (_, i) {
      if (i % 2 !== 0) {
        d3.select(this).select("line").attr("y2", 15);
        d3.select(this).select("text").attr("dy", "1.91em");
      }
    });
    context.stripPlotConfig.yAxisGroup.selectAll(".tick").each(function (_, i) {
      if (i % 2 !== 0) {
        d3.select(this).select("line").attr("x2", -15);
        d3.select(this).select("text").attr("dy", "-2.41em");
      }
    });

    // JOIN data selection using Primary Key label
    let dataBound = context.stripPlotConfig.stripsGroup
      .selectAll(".post")
      .data(prepared, (d) => d[dataset["primaryKey"]]);

    // DELETE extra strips (fade them out)
    dataBound.exit().remove();

    // ENTER new strips (starting invisible, later fade them in)
    let enterSelection = dataBound.enter().append("g").classed("post", true);

    // UPDATE all existing strips
    enterSelection.append("line");
    enterSelection
      .merge(dataBound)
      .select("line")
      .attr("x1", (d) => {
        const x = context.stripPlotConfig.xScale;
        return yAxisMajor
          ? xAxisCategorical
            ? x(d["xVar"]) + x.bandwidth() / 2 - 0.025 * context.plotWidth
            : 0.025 * context.plotWidth // 25% of gridline
          : x(d["xVar"]);
      })
      .attr("x2", (d) => {
        const x = context.stripPlotConfig.xScale;
        return yAxisMajor
          ? xAxisCategorical
            ? x(d["xVar"]) + x.bandwidth() / 2 + 0.025 * context.plotWidth
            : 0.075 * context.plotWidth // 75% of gridline
          : x(d["xVar"]);
      })
      .attr("y1", (d) => {
        const y = context.stripPlotConfig.yScale;
        return !yAxisMajor
          ? yAxisCategorical
            ? y(d["yVar"]) + y.bandwidth() / 2 - 0.025 * context.plotHeight
            : 0.925 * context.plotHeight // 25% of gridline
          : y(d["yVar"]);
      })
      .attr("y2", (d) => {
        const y = context.stripPlotConfig.yScale;
        return !yAxisMajor
          ? yAxisCategorical
            ? y(d["yVar"]) + y.bandwidth() / 2 + 0.025 * context.plotHeight
            : 0.975 * context.plotHeight // 75% of gridline
          : y(d["yVar"]);
      })
      .style("stroke", (d) => {
        // use dict OBJECT to update source data by reference!
        let dataPoint = originalDatasetDict[d[dataset["primaryKey"]]];
        context.utilsService.colorDataPoint(context, dataPoint, prepared);
        // default/selected stroke is DIFFERENT than data point color!!
        if (dataPoint["selected"]) {
          return "brown"; // selected color
        } else if (dataPoint["timesVisited"] > 0 && dataset["colorByMode"]) {
          return dataPoint["color"]; // bias color
        } else {
          return "black"; // default color
        }
      })
      .style("stroke-width", (d) => (d["selected"] ? "2px" : "1px"))
      .style("cursor", "pointer")
      .on("click", function (event, d) {
        if (context.global.appType === "ADMIN") {
          d["selected"]
            ? context.utilsService.clickRemoveItem(context, event, d)
            : context.utilsService.clickAddItem(context, event, d);
        }
      })
      .on("mouseover", function (event, d) {
        context.utilsService.mouseoverItem(context, event, d, this, "stroke");
      })
      .on("mouseout", function (event, d) {
        context.utilsService.mouseoutItem(context, event, d);
      });
  }
}
