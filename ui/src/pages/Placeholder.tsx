export default function Placeholder(props: {
  title: string;
  day: string;
  summary: string;
  items: string[];
}) {
  return (
    <>
      <header className="page-head">
        <div>
          <h1>{props.title}</h1>
          <p className="muted">Not built yet · planned for {props.day}</p>
        </div>
      </header>
      <div className="panel placeholder">
        <p>{props.summary}</p>
        <ul>
          {props.items.map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
      </div>
    </>
  );
}
