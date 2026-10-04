import Placeholder from "./Placeholder";

export default function Forecasting() {
  return (
    <Placeholder
      title="Forecasting"
      day="Day 5-6"
      summary="Quantile demand forecasts and the federated-learning comparison."
      items={[
        "P10/P50/P90 band against actuals for any post and class",
        "Item-wise table: stock, 30-day P90 demand, recommended quantity",
        "Federated panel: local vs federated vs central error per formation",
      ]}
    />
  );
}
